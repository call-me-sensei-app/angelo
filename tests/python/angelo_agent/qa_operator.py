# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A stand-in ``self`` for calling an operator's method directly (GUI suites, 1.0.5).

A suite that drives a modal operator's ``modal`` with synthetic events calls it on the class with a hand-made ``self``
holding the state ``invoke`` would set. A bare ``SimpleNamespace`` has none of the class's other methods, so the first
helper method a modal gains breaks every suite that calls it (test_blender_input_capture on 6ceaef365ff:
``ANGELO_OT_studio_input.modal`` gained ``self.moved_out``). ``stand_in`` carries everything the operator class and
its Angelo-side bases define (methods, properties, class defaults), so the modal runs against the class as it is.
Blender's own bases (``bpy.types.Operator``, ``bpy_struct``) are left out: their methods need a registered instance.
``test_native_operator_stand_ins`` keeps every suite on this helper. Needs no Blender (host tests import it).
"""

from types import SimpleNamespace


def _own(klass):
    module = getattr(klass, "__module__", "") or ""
    return klass is not object and module != "builtins" and not module.startswith(("bpy", "_bpy"))


def stand_in(cls, **attributes):
    """An object that behaves as an instance of ``cls`` for its own Python code, with ``attributes`` as its state."""
    namespace = {}
    for klass in reversed(cls.__mro__):   # Most derived last, so an override wins as on a real instance.
        if _own(klass):
            namespace.update((name, value) for name, value in vars(klass).items()
                             if not (name.startswith("__") and name.endswith("__")))
    namespace["__repr__"] = lambda self: f"stand_in({cls.__name__}, {vars(self)!r})"
    return type(f"{cls.__name__}StandIn", (SimpleNamespace,), namespace)(**attributes)
