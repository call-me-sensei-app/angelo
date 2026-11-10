# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a file Angelo opens for the person may run the Python it carries: its own default, never the session's.

``wm.open_mainfile`` run with a path and no ``use_scripts`` keeps the trust of the file already open (Blender's
"Fix: Core: Auto-run scripts trusted for unintended files": Open Recent and macOS opens now take the file's own
default instead). So every open of Angelo's passes ``use_scripts``: the person's own file (the start card's recent
scenes, an engine's linked .blend) with ``default`` (what Blender's file browser and Open Recent preselect for it),
and Angelo's private copies with False. ``test_native_open_trust`` keeps every call site to that. A reviewed agent
script's own ``open_mainfile(filepath=...)`` gets the same default while it runs (``Scripts``, on its ``bpy.ops`` as
``script_operator_time.Clock`` is). No bpy at import.
"""

OPEN = ("wm", "open_mainfile")


def default(path):
    """Blender's default "Trusted Source" for opening ``path`` (``BKE_autoexec_default_trust_source``): the command
    line's ``--enable/--disable-autoexec`` when given, else Auto Run Python Scripts outside its excluded paths."""
    import bpy
    try:
        override = getattr(bpy.app, "autoexec_override", None)
        if override is not None:
            return bool(override)
        if not bpy.context.preferences.filepaths.use_scripts_auto_execute:
            return False
        return bool(bpy.path.is_autoexec(path, canonicalize=True, strip_filename=True))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return False   # Unreadable: the file opens without running its scripts (fail closed).


class _Open:
    """``wm.open_mainfile`` as the script called it, with the file's own default trust when it named a file and no
    ``use_scripts``; everything else is the operator's own."""

    __slots__ = ("_operator", "_scripts")

    def __init__(self, operator, scripts):
        self._operator, self._scripts = operator, scripts

    def __call__(self, *args, **kwargs):
        if self._scripts.active and "use_scripts" not in kwargs and isinstance(kwargs.get("filepath"), str):
            kwargs["use_scripts"] = default(kwargs["filepath"])
        return self._operator(*args, **kwargs)

    def __getattr__(self, name):
        if name in _Open.__slots__:
            raise AttributeError(name)
        return getattr(self._operator, name)

    def __repr__(self):
        return repr(self._operator)


class Scripts:
    """While a script runs, its ``bpy.ops.wm.open_mainfile`` calls take the opened file's default trust; a context
    manager stacked on ``_op_create_function`` and put back however the script ends."""

    def __init__(self, bpy):
        self._bpy, self._real, self.active = bpy, None, False

    def __enter__(self):
        ops = getattr(self._bpy, "ops", None)
        real = getattr(ops, "_op_create_function", None)
        if real is None:   # A stand-in bpy.
            return self

        def create(module, function):
            made = real(module, function)
            return _Open(made, self) if (module, function) == OPEN else made
        ops._op_create_function = create
        self._real, self.active = real, True
        return self

    def __exit__(self, *_exception):
        self.active = False
        if self._real is not None:
            self._bpy.ops._op_create_function, self._real = self._real, None
        return False
