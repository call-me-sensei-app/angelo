# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""Angelo: game physics in glTF (doc/angelo/game_physics.rst).

``Object.angelo_physics`` holds what an object is to a game engine (a static, rigid, kinematic or trigger body, and a
collision shape fitted to its mesh or sized by hand). Every glTF export writes it as the standard OMI_physics_body and
OMI_physics_shape extensions, which Godot 4.4 and newer import as physics nodes (``export``), and every glTF import
reads them back (``imports``). An export or import with no physics in it is unchanged.

It lives in the glTF add-on, not in Angelo's agent add-on, because this add-on is enabled in every session: the
agent's add-on isn't enabled in a background Angelo, which is how Godot imports a .blend through Angelo and how the
engine link writes a .glb back.
"""

from . import fitting, props


def export_extension():
    from .export import PhysicsExportExtension
    return PhysicsExportExtension()


def import_extension():
    from .imports import PhysicsImportExtension
    return PhysicsImportExtension()


def register():
    props.register()
    fitting.register()


def unregister():
    fitting.unregister()
    props.unregister()
