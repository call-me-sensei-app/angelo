# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script that replaced the open document: the host reads nothing it held from before.

A script can load, revert or recover a file, or read the startup file, under any name, a computed one included
(``bpy.ops.wm.open_mainfile``, ``revert_mainfile``, ``read_homefile``, ``read_factory_settings``, ``recover_*``).
That frees Main, and in a background Blender (the headless child, a parallel worker) the window manager and the
window the executor holds as well. Up to 1.0.5 the executor then read the scene back through that window
(``blender_tools``: ``self.window.view_layer.update()``) and the process crashed, a use-after-free. Even where
nothing crashed, the read-back described objects of a file that was gone, and the apply operator pushed an Undo step
into the file the script loaded.

``Watch`` runs while the script does (``script_after.Observing``). A load that succeeded fires Blender's
``load_post`` (a failed one fires ``load_post_fail`` and replaces nothing), and its persistent handler records it.
A script that removed the handler counts as having loaded, since what it hid is unknown, and so does a new window
manager. Every host object the script can reach stops once the document is gone: hooks the host inserts into the
script (``script_prepare``'s parent-inverse view layer) call ``guard`` first, and the ``kit`` is ``Guarded``. Blender
invalidates the Python objects of the IDs a load frees (``ReferenceError``), so ``scene`` and the script's own IDs
raise rather than reach freed memory. After the script, ``Observing`` raises ``Replaced``, a ``BaseException`` the script's own failure
handling (``script_errors.SCRIPT_FAILURES``) never catches, so neither the success read-back nor the rollback runs.
``run`` turns it into ``DOCUMENT_REPLACED``: a host decision that ends the request (``plan_recovery``), so the person
looks first; the apply operator cancels its Undo push. An Undo or Redo inside the script is not a replacement
(``script_watch`` covers those). Main thread only.
"""

DOCUMENT_REPLACED = {
    "error": "The script loaded or replaced the open file, so the host read nothing back and Undo cannot take it "
             "back; what it changed is unverified.",
    "error_code": "DOCUMENT_REPLACED", "document_replaced": True, "main_data_restored": False,
    "partial_changes_possible": True, "scene_changed": True}
_active = None   # The running script's Watch (``start`` keeps the one it interrupted: a script run from a script).


class Watch:
    """Notices a document replacement between ``start`` and ``stop`` (module docstring)."""

    def __init__(self, bpy):
        self.bpy, self.loaded, self._handlers, self._manager, self._outer = bpy, False, None, None, None

        def seen(*_args):
            self.loaded = True

        # Persistent: a file load would otherwise remove it before load_post could fire.
        handlers = getattr(getattr(bpy, "app", None), "handlers", None)
        self._seen = handlers.persistent(seen) if hasattr(handlers, "persistent") else seen

    def _pointer(self):
        manager = getattr(getattr(self.bpy, "context", None), "window_manager", None)
        return manager.as_pointer() if hasattr(manager, "as_pointer") else None

    def start(self):
        global _active
        self._handlers = getattr(getattr(getattr(self.bpy, "app", None), "handlers", None), "load_post", None)
        if self._handlers is not None:
            self._handlers.append(self._seen)
        self._manager, self._outer, _active = self._pointer(), _active, self
        return self

    @property
    def replaced(self):
        removed = self._handlers is not None and not any(item is self._seen for item in self._handlers)
        return self.loaded or removed or self._pointer() != self._manager

    def stop(self):
        """Whether the document was replaced; idempotent."""
        global _active
        replaced = self.replaced
        while self._handlers is not None and any(item is self._seen for item in self._handlers):
            self._handlers.remove(self._seen)
        if _active is self:
            _active = self._outer
        self.loaded = self.loaded or replaced
        return replaced


def guard():
    """For host hooks inside a running script: raise ``Replaced`` once its document is gone, before they touch
    anything they held from before."""
    if _active is not None and _active.replaced:
        raise Replaced("")


class Guarded:
    """A host object handed to the script (the ``kit``) that stops once the document is gone: every attribute the
    script reads calls ``guard`` first, so nothing it held from before is touched, whatever the object holds."""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name):
        guard()
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name, value):
        guard()
        setattr(object.__getattribute__(self, "_target"), name, value)


class Replaced(BaseException):
    """Raised past the script's own failure handling once its document is gone; carries the tool result."""

    def __init__(self, output, raised=None):
        super().__init__("document replaced")
        self.result = dict(DOCUMENT_REPLACED, output=output)
        if raised is not None:
            self.result["raised"] = raised


def run(execute, *arguments):
    """``execute(*arguments)``, or the ``DOCUMENT_REPLACED`` result of a script that replaced the document."""
    try:
        return execute(*arguments)
    except Replaced as replaced:
        return replaced.result
