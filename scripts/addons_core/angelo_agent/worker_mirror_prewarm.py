# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The runtime prewarm of a chat its worker hosts (design A′): started in the worker while the person writes here.

``runtime_prewarm.observe`` treats a worker chat's mirror as any chat: the draft's changes, the idle bound, the launch
key and ``MAX_PREWARMED`` across every chat (a mirror's record counts) are its own. Only the runtime is not started
here (a mirror holds no provider or key): ``start`` sends ``chat.prewarm`` to the worker, whose chat starts its own
(``WorkerChatHost._warm``: its launch goes through Angelo's runtime service, C5), and the record's release sends
``release``. A draft that keeps changing keeps the worker's prewarm too (``TOUCH_SECONDS``). The person's Send drops
the record here without a release: the worker's request adopts its runtime, or releases one that no longer matches.
Every op is sent off the main thread and fails closed (nothing assumed started). Standard library only, no bpy.
"""

import threading
from dataclasses import dataclass

from .runtime_prewarm import Prewarm

TOUCH_SECONDS = 30.0   # A draft still being written keeps the worker's prewarm at most this often.


class Remote:
    """The record's provider and owner: the worker's prewarm of ``mirror``'s chat."""

    remote = True

    def __init__(self, mirror):
        self.mirror = mirror

    def release(self):
        pass   # ``close`` (``runtime_prewarm.release``'s thread) tells the worker.

    def close(self):
        _send(self.mirror, "release")


@dataclass(eq=False)
class RemotePrewarm(Prewarm):
    sent: float = 0.0

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "touched" and value - self.__dict__.get("sent", value) >= TOUCH_SECONDS:
            super().__setattr__("sent", value)
            _later(self.provider.mirror, "start")


def start(mirror, launch_key, now):
    """``runtime_prewarm.start`` for a mirror: the record, or None when its chat is not open in a worker."""
    if not callable(getattr(mirror, "send_op", None)):
        return None
    handle = Remote(mirror)
    mirror.prewarmed = RemotePrewarm(handle, handle, launch_key, now, sent=now)
    _later(mirror, "start")
    return mirror.prewarmed


def sent(mirror):
    """The person's message went to the worker: its request adopts the prewarm there (nothing to release here)."""
    mirror.prewarmed = None


def _later(mirror, action):
    threading.Thread(target=_send, args=(mirror, action), daemon=True, name="AngeloWorkerPrewarm").start()


def _send(mirror, action):
    try:
        mirror._op("chat.prewarm", action=action)
    except Exception as error:  # noqa: BLE001 - only a head start: the request launches its own.
        print("Angelo: a worker chat's prewarm (%s) was not taken: %s" % (action, error))
