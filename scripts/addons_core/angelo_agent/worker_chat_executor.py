# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted worker chat's executor (design A′): the window's own, kept to the worker's part.

A chat its worker hosts (``worker_chat_host``) runs its calls on the worker's document with the window's
``BlenderExecutor``, built wherever the window builds one (the tool pump, the approve and apply operators). Every one
reaches the chat through ``controller_external.bound_target``, which gives a hosted chat ``confined(executor)``
instead, as it gives a lane chat its ``RemoteExecutor``. That executor is the given one, with what a worker chat's
executor does on the lane path:

* a call the worker does not run is refused before its card, with nothing run (``worker_refusals.refusal``, the
  lane path's own set): a job, Sculpt, Studio, a PREVIEW, a script that would load another file;
* a view is drawn only after the worker's GPU check (``WorkerHost.gpu``, the lane view route's), which also
  initializes Blender's GPU module for the background window (FE native defect D7: "gpu module not initialized");
  the check takes Angelo's verdict for this binary from ``chat.open`` (``gpu_verdict``) and probes only without one;
* after a script, a load the name check could not see is contained (``worker_script.contain``): the part is loaded
  again as last saved and nothing is saved over it, or the worker ends; a script that saved the open file as another
  file has the part saved again (``WorkerChatHost.save_due``).

Everything else (attributes the controller sets, such as ``studio_context`` and ``observation_store``, and every
other method) is the given executor's own.
"""

import functools

from . import worker_refusals as refusals
from .worker_script import SCRIPT


class ConfinedExecutor:
    def __init__(self, executor, chat):
        object.__setattr__(self, "inner", executor)
        object.__setattr__(self, "chat", chat)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __setattr__(self, name, value):
        setattr(self.inner, name, value)

    def _host(self):
        """The worker's ``WorkerHost``; a chat no longer hosted runs nothing here (fail closed)."""
        host = getattr(getattr(self.chat, "chat_host", None), "host", None)
        if host is None:
            raise refusals.nothing_ran(RuntimeError("This chat is no longer hosted by its worker; nothing ran"))
        return host

    def precondition(self, name, arguments):
        refusals.admit(name, arguments)
        return self.inner.precondition(name, arguments)

    @property
    def start_native_job(self):
        """The given executor's, refused first; with its own signature, from which ``program_completion`` picks the
        keywords it passes (a ``**`` of this wrapper's would claim every one)."""
        inner = self.inner.start_native_job

        @functools.wraps(inner)
        def start(name, arguments, *args, **options):
            refusals.admit(name, arguments)
            return inner(name, arguments, *args, **options)
        return start

    def execute(self, name, arguments):
        refusals.admit(name, arguments)
        if refusals.draws(name):
            self._host().gpu({"gpu_verdict": getattr(self.chat, "gpu_verdict", None)})   # Raises the worker's refusal, nothing drawn, on a machine that cannot draw offscreen.
        if name != SCRIPT:
            return self.inner.execute(name, arguments)
        from .worker_script import contain, on_part, serial
        host = self._host()
        before = serial(host.ui)
        result, reloaded = contain(host, self.inner.execute(name, arguments), before)
        if not reloaded and not on_part(host):
            self.chat.chat_host.save_due = True   # Its open file is another file: the part is saved again.
        return result


def confined(executor, chat):
    """``executor`` for ``chat``, a chat its worker hosts (module docstring); None stays None."""
    if executor is None or isinstance(executor, ConfinedExecutor):
        return executor
    return ConfinedExecutor(executor, chat)
