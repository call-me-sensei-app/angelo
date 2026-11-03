# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""``Object.angelo_physics`` and ``Scene.angelo_physics_format``: the physics intent the glTF exporter writes.

No property has a get, set or update callback, every enum's items are constant and none can be keyed (physics is
not animation), so writing one runs no Python:
the agent's ``property_set`` admits these paths (``angelo_agent.rna_rules.PROVEN_RUNTIME``). Enum items carry fixed
numbers because the .blend stores the number: never renumber one.
"""

import bpy

from . import shapes

# Blender's catalogue answers a label first, and some of these words mean other things there ("Manual" is the
# handbook, "Box" a box select in some languages), so every label here is Angelo's own, in Angelo's message context.
ANGELO = "Angelo"
SETTING = {"options": set(), "translation_context": ANGELO}   # Not keyable (physics isn't animation); Angelo's labels.

BODY_ITEMS = (
    ("NONE", "None", "No physics body of its own", 0),
    ("STATIC", "Static", "Collides but never moves: level geometry, walls, floors", 1),
    ("RIGID", "Rigid", "Moved by the physics simulation: props that fall, roll and get pushed", 2),
    ("KINEMATIC", "Kinematic", "Collides and is moved by animation or scripts: platforms, doors", 3),
    ("TRIGGER", "Trigger", "Detects what enters it and never collides: pickups, zones, checkpoints", 4),
)
SHAPE_ITEMS = (
    ("NONE", "None", "No collision shape", 0),
    ("BOX", "Box", "A box", 1),
    ("SPHERE", "Sphere", "A sphere", 2),
    ("CAPSULE", "Capsule", "A cylinder with rounded ends, the usual character shape", 3),
    ("CYLINDER", "Cylinder", "A cylinder; not every engine has one, so engines may approximate it", 4),
    ("CONVEX", "Convex Hull", "The mesh's convex hull: fast and fits most props", 5),
    ("TRIMESH", "Mesh", "The exact mesh: for static level geometry only, slowest to collide", 6),
)
FIT_ITEMS = (
    ("AUTO", "Fit to Mesh", "Fit the shape to the object's geometry whenever it's exported", 0),
    ("MANUAL", "Manual", "Use the dimensions below", 1),
)
AXIS_ITEMS = (
    ("AUTO", "Longest", "Along the object's longest dimension (Z when fitting manually)", 0),
    ("X", "X", "Along the object's X axis", 1),
    ("Y", "Y", "Along the object's Y axis", 2),
    ("Z", "Z", "Along the object's Z axis", 3),
)
FORMAT_ITEMS = (
    ("OMI", "Standard glTF (OMI)", "OMI_physics_body and OMI_physics_shape, which Godot 4.4 and newer import", 0),
    ("GODOT_SUFFIXES", "Godot Name Suffixes",
     "Godot's -col, -convcol and -rigid name suffixes, for older pipelines; they can't express every shape", 1),
)


class AngeloPhysicsSettings(bpy.types.PropertyGroup):
    body: bpy.props.EnumProperty(**SETTING, name="Body", items=BODY_ITEMS, default="NONE",
                                 description="How the game engine treats this object")
    mass: bpy.props.FloatProperty(**SETTING, name="Mass", default=1.0, min=0.001, soft_max=10000.0, unit='MASS',
                                  description="The rigid body's mass")
    shape: bpy.props.EnumProperty(**SETTING, name="Shape", items=SHAPE_ITEMS, default="NONE",
                                  description="The collision shape. Without a body, a shape makes a static collider")
    fit: bpy.props.EnumProperty(**SETTING, name="Size", items=FIT_ITEMS, default="AUTO",
                                description="Whether the shape follows the mesh or uses the dimensions below")
    axis: bpy.props.EnumProperty(**SETTING, name="Axis", items=AXIS_ITEMS, default="AUTO",
                                 description="The axis a capsule or cylinder runs along")
    size: bpy.props.FloatVectorProperty(**SETTING, name="Box Size", size=3, default=(1.0, 1.0, 1.0), min=0.0,
                                        subtype='XYZ_LENGTH', unit='LENGTH', description="The box's size")
    radius: bpy.props.FloatProperty(**SETTING, name="Radius", default=0.5, min=0.0, unit='LENGTH',
                                    description="The sphere's, capsule's or cylinder's radius")
    height: bpy.props.FloatProperty(**SETTING, name="Height", default=2.0, min=0.0, unit='LENGTH',
                                    description="The capsule's or cylinder's full height, end to end")
    offset: bpy.props.FloatVectorProperty(**SETTING, name="Center", size=3, default=(0.0, 0.0, 0.0),
                                          subtype='TRANSLATION', unit='LENGTH',
                                          description="The shape's centre in the object's own axes")
    collision_only: bpy.props.BoolProperty(
        **SETTING, name="Collision Only", default=False,
        description="Export this mesh only as a collision shape, not as a visible model (Godot's -colonly)")


def stored(obj):
    """The object's stored physics values ({} when none were ever set), read without creating storage: reading
    ``obj.angelo_physics`` creates it, and an export must never change the file it reads."""
    system = obj.bl_system_properties_get()
    group = system.get("angelo_physics") if system is not None else None
    return group if group is not None else {}


def marked(obj):
    """Whether the object has a body or a shape: the exporter's only cost for every unmarked object."""
    group = stored(obj)
    return bool(group) and (group.get("body", 0) != 0 or group.get("shape", 0) != 0)


def scene_format(scene):
    group = scene.bl_system_properties_get() if scene is not None else None
    value = group.get("angelo_physics_format", 0) if group is not None else 0
    return FORMAT_ITEMS[value][0] if 0 <= value < len(FORMAT_ITEMS) else "OMI"


assert tuple(item[0] for item in BODY_ITEMS) == shapes.BODIES
assert tuple(item[0] for item in SHAPE_ITEMS) == shapes.SHAPES


def register():
    bpy.utils.register_class(AngeloPhysicsSettings)
    bpy.types.Object.angelo_physics = bpy.props.PointerProperty(type=AngeloPhysicsSettings, name="Game Physics")
    bpy.types.Scene.angelo_physics_format = bpy.props.EnumProperty(
        **SETTING, name="Physics Format", items=FORMAT_ITEMS, default="OMI",
        description="How glTF exports write the objects' game physics")


def unregister():
    del bpy.types.Scene.angelo_physics_format
    del bpy.types.Object.angelo_physics
    bpy.utils.unregister_class(AngeloPhysicsSettings)
