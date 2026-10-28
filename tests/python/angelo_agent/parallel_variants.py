# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Other ways to build the parallel-agents one-shot tests' scenes, graded by their own checks (``oneshot_variants``).

Each variant starts from the test's stand-in (``parallel_kits``) and either makes something another way, as one
agent or a merge of several workers' parts would (it must pass every check), or breaks one thing the brief asks for
(it must fail exactly the checks for it). The ways a merge leaves a scene are here on purpose: copies that are
linked duplicates in a collection of their own, collection instances, a design whose objects keep their own names,
so a check never favours how the work was split. A king and a queen differ by 14 % in height, inside a probe's
tolerance, so which of them stands on d1 is the vision review's to grade (``bench_parallel_grade``), not a check's.
"""

import math

import parallel_kits as kits


def _stand_in(name):
    import scenario_specs

    def build(bpy):
        scenario_specs.module(name).build(bpy)
    return build


def _objects(bpy, prefix):
    return [obj for obj in bpy.data.objects if obj.name.startswith(prefix)]


def chess_merged_collections(bpy):
    """As a merge leaves it: each design in a collection named after its part, copies sharing its mesh, and the
    objects' names the workers gave ('Pawn', 'Pawn.001', ...)."""
    _stand_in("oneshot_chess")(bpy)
    scene = bpy.context.scene
    for piece in kits.HEIGHTS:
        collection = bpy.data.collections.new(piece)
        scene.collection.children.link(collection)
        for obj in _objects(bpy, "White " + piece) + _objects(bpy, "Black " + piece):
            for owner in list(obj.users_collection):
                owner.objects.unlink(obj)
            collection.objects.link(obj)
            obj.name = piece


def chess_collection_instances(bpy):
    """Each design once, hidden, and every piece an empty instancing its collection."""
    _stand_in("oneshot_chess")(bpy)
    scene = bpy.context.scene
    for piece in kits.HEIGHTS:
        for colour in ("White", "Black"):
            found = _objects(bpy, "%s %s" % (colour, piece))
            source = bpy.data.collections.new("%s %s Design" % (colour, piece))
            scene.collection.children.link(source)
            master = found[0].copy()
            master.location, master.rotation_euler = (0.0, 0.0, -1.0), (0.0, 0.0, 0.0)
            source.objects.link(master)
            scene.view_layers[0].layer_collection.children[source.name].exclude = True
            for obj in found:
                holder = bpy.data.objects.new(obj.name + " Place", None)
                holder.instance_type, holder.instance_collection = "COLLECTION", source
                holder.location, holder.rotation_euler = obj.location.copy(), obj.rotation_euler.copy()
                holder.location.z += 1.0   # The design sits 1 m down in its collection.
                scene.collection.objects.link(holder)
                bpy.data.objects.remove(obj)


def chess_missing_knight(bpy):
    _stand_in("oneshot_chess")(bpy)
    x, y = kits.square_centre("g1")
    bpy.data.objects.remove(next(o for o in _objects(bpy, "White Knight")
                                 if abs(o.location.x - x) < 1e-6 and abs(o.location.y - y) < 1e-6))


def chess_giant_kings(bpy):
    _stand_in("oneshot_chess")(bpy)
    for obj in _objects(bpy, "White King") + _objects(bpy, "Black King"):
        obj.scale = (1.0, 1.0, 1.5)


def chess_piece_in_the_middle(bpy):
    _stand_in("oneshot_chess")(bpy)
    pawn = _objects(bpy, "White Pawn")[0]
    pawn.location.x, pawn.location.y = kits.square_centre("e4")


def parlour_merged_chairs(bpy):
    """The dining chairs and armchairs as a merge places one design: linked copies in a collection, rotated."""
    _stand_in("oneshot_parlour")(bpy)
    collection = bpy.data.collections.new("Dining Chair")
    bpy.context.scene.collection.children.link(collection)
    for obj in _objects(bpy, "Dining Chair"):
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
        obj.name = "Chair"


def _instanced(bpy, prefix):
    """Every object named ``prefix`` replaced by an empty instancing one hidden design of it (a merge's copies could
    be these as well)."""
    found = _objects(bpy, prefix)
    scene = bpy.context.scene
    source = bpy.data.collections.new(prefix + " Design")
    scene.collection.children.link(source)
    master = found[0].copy()
    master.location, master.rotation_euler = (0.0, 0.0, -50.0), (0.0, 0.0, 0.0)
    source.objects.link(master)
    scene.view_layers[0].layer_collection.children[source.name].exclude = True
    for obj in found:
        holder = bpy.data.objects.new(obj.name + " Place", None)
        holder.instance_type, holder.instance_collection = "COLLECTION", source
        holder.matrix_world = obj.matrix_world.copy()
        holder.location.z += 50.0
        scene.collection.objects.link(holder)
        bpy.data.objects.remove(obj)


def parlour_instanced_chairs(bpy):
    _stand_in("oneshot_parlour")(bpy)
    _instanced(bpy, "Dining Chair")


def roadster_instanced_wheels(bpy):
    _stand_in("oneshot_roadster")(bpy)
    _instanced(bpy, "Car Wheel")


def outfit_merged_shoes(bpy):
    """The shoes as a merge places one design: linked copies in a collection of their own."""
    _stand_in("oneshot_outfit")(bpy)
    collection = bpy.data.collections.new("Shoe")
    bpy.context.scene.collection.children.link(collection)
    for obj in _objects(bpy, "Character Shoe"):
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)


def outfit_hat_parented(bpy):
    """The hat parented to the body, as a character's accessory often is."""
    _stand_in("oneshot_outfit")(bpy)
    hat, body = bpy.data.objects["Character Hat"], bpy.data.objects["Character Body"]
    hat.parent = body
    hat.matrix_parent_inverse = body.matrix_world.inverted()


def parlour_three_chairs(bpy):
    _stand_in("oneshot_parlour")(bpy)
    bpy.data.objects.remove(_objects(bpy, "Dining Chair")[-1])


def parlour_low_table(bpy):
    _stand_in("oneshot_parlour")(bpy)
    _objects(bpy, "Dining Table")[0].scale.z = 0.6


def roadster_wheel_parts(bpy):
    """Each wheel as a tyre and a hub of its own, the hub parented to the tyre (a design of two objects copied)."""
    _stand_in("oneshot_roadster")(bpy)
    for index, wheel in enumerate(_objects(bpy, "Car Wheel")):
        hub = bpy.data.objects.new("Car Wheel Hub %d" % index, wheel.data)
        hub.scale = (0.6, 1.05, 0.6)
        bpy.context.scene.collection.objects.link(hub)
        hub.parent = wheel


def roadster_three_wheels(bpy):
    _stand_in("oneshot_roadster")(bpy)
    bpy.data.objects.remove(_objects(bpy, "Car Wheel")[-1])


def roadster_wheels_inside(bpy):
    _stand_in("oneshot_roadster")(bpy)
    for wheel in _objects(bpy, "Car Wheel"):
        wheel.location.y *= 0.5


def outfit_one_shoe(bpy):
    _stand_in("oneshot_outfit")(bpy)
    bpy.data.objects.remove(_objects(bpy, "Character Shoe")[-1])


def outfit_shirt_joined_to_body(bpy):
    """The shirt is no longer its own object: the brief asks for parts that can be changed one at a time."""
    _stand_in("oneshot_outfit")(bpy)
    shirt = bpy.data.objects["Character Shirt"]
    shirt.name = "Character Body Shirt"


EVERY_KING = ["a white king on e1, 9.5 cm tall", "a black king on e8, 9.5 cm tall"]
VARIANTS = {
    "oneshot_chess": (
        ("the stand-in", _stand_in("oneshot_chess"), ()),
        ("as a merge leaves it: collections per design, worker names", chess_merged_collections, ()),
        ("every piece a collection instance of its design", chess_collection_instances, ()),
        ("no knight on g1", chess_missing_knight, ("a white knight on g1, 6.0 cm tall",)),
        ("kings half as tall again", chess_giant_kings, tuple(EVERY_KING)),
        ("a pawn moved from a2 to e4", chess_piece_in_the_middle, ("a white pawn on a2, 4.6 cm tall", "e4 is empty")),
    ),
    "oneshot_parlour": (
        ("the stand-in", _stand_in("oneshot_parlour"), ()),
        ("dining chairs as a merge places them", parlour_merged_chairs, ()),
        ("dining chairs as collection instances of one design", parlour_instanced_chairs, ()),
        ("three dining chairs", parlour_three_chairs, ("a dining chair seat 0.45 m at (0.45, -2)",)),
        ("a dining table 0.45 m high", parlour_low_table, ("the dining table top 0.75 m at (0, -1.35)",)),
    ),
    "oneshot_roadster": (
        ("the stand-in", _stand_in("oneshot_roadster"), ()),
        ("wheels of a tyre and a parented hub", roadster_wheel_parts, ()),
        ("wheels as collection instances of one design", roadster_instanced_wheels, ()),
        ("three wheels", roadster_three_wheels, ("a wheel at (-1.3, 0.78), 0.33 m up",)),
        ("wheels pushed in under the body", roadster_wheels_inside,
         tuple("a wheel at (%g, %g), 0.33 m up" % at for at in kits.WHEELS)),
    ),
    "oneshot_outfit": (
        ("the stand-in", _stand_in("oneshot_outfit"), ()),
        ("shoes as a merge places them", outfit_merged_shoes, ()),
        ("the hat parented to the body", outfit_hat_parented, ()),
        ("one shoe", outfit_one_shoe, ("two shoes",)),
        ("the shirt not its own object", outfit_shirt_joined_to_body,
         ("the shirt is its own object", "the shirt from the waist to the neck", "an orange shirt")),
    ),
}
assert math.isclose(kits.SQUARE * 8, 0.4)
