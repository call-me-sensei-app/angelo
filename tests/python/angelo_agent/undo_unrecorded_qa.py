# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What test_blender_undo_unrecorded_change and test_blender_undo_unrecorded_modes share: recorded steps as a person's
operators make them, the person's Undo and Redo keys, and the read of the whole scene that a reused ID with a stale
pointer breaks (the dependency graph, the outliner drawn, every object's data, collection and view layer)."""

import bpy

# Object types whose data's ID type has another name (every other type's data has the object's type).
DATA_TYPE = {"SURFACE": "CURVE", "FONT": "CURVE"}


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    areas = [a for a in window().screen.areas if a.type == 'VIEW_3D']
    area = max(areas, key=lambda a: a.width * a.height)
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def override():
    area, region = view3d()
    return bpy.context.temp_override(window=window(), area=area, region=region)


def ensure_outliner():
    """An outliner drawn throughout, as in Blender's Layout: the crash read the scene there."""
    if not any(a.type == 'OUTLINER' for a in window().screen.areas):
        spare = min((a for a in window().screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height)
        spare.ui_type = 'OUTLINER'


def key(kind, **modifiers):
    _area, region = view3d()
    x, y = region.x + region.width // 2, region.y + region.height // 2
    w = window()
    w.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    w.event_simulate(type=kind, value='PRESS', x=x, y=y, **modifiers)
    w.event_simulate(type=kind, value='RELEASE', x=x, y=y, **modifiers)


def press(idname, keymap_name="Screen", **properties):
    """The person's key for ``idname`` in ``keymap_name`` (Undo, Redo, Select None...), as their keymap binds it."""
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps[keymap_name]
    item = next(i for i in keymap.keymap_items if i.idname == idname and i.active and i.map_type == 'KEYBOARD'
                and all(getattr(i.properties, name) == value for name, value in properties.items()))
    key(item.type, **{name: getattr(item, name) == 1 for name in ("ctrl", "alt", "shift", "oskey")})


def cube_x():
    cube = bpy.data.objects.get("Cube")
    return None if cube is None else round(cube.location.x, 4)


def recorded_move(name="Cube"):
    """A change with its own undo step, as a person's move makes one."""
    target = bpy.data.objects[name]
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj == target)
    bpy.context.view_layer.objects.active = target
    with override():
        bpy.ops.transform.translate('EXEC_DEFAULT', True, value=(1.0, 0.0, 0.0))


def recorded_add(operator, **properties):
    """An object added with its own undo step, as Shift A makes one; the new object."""
    module, name = operator.split(".")
    with override():
        getattr(getattr(bpy.ops, module), name)('EXEC_DEFAULT', True, **properties)
    return bpy.context.view_layer.objects.active


def recorded_operator(operator, **properties):
    module, name = operator.split(".")
    with override():
        getattr(getattr(bpy.ops, module), name)('EXEC_DEFAULT', True, **properties)


def push(message):
    """A step holding the scene as it is now (``ed.undo_push``, as an add-on does after its own change)."""
    with override():
        bpy.ops.ed.undo_push(message=message)


def write_library(path, names):
    """A library file holding one mesh object per name; nothing of it stays in this file."""
    made = [bpy.data.objects.new(name, bpy.data.meshes.new(name)) for name in names]
    bpy.data.libraries.write(str(path), set(made), fake_user=True)
    for obj in made:
        mesh = obj.data
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
    return path


def link(path, name, scene):
    """``name`` linked from the library at ``path`` (already linked or not) into ``scene``'s collection."""
    with bpy.data.libraries.load(str(path), link=True) as (_source, target):
        target.objects = [name]
    obj = target.objects[0]
    scene.collection.objects.link(obj)
    return obj


def shape_keys_add(*names):
    """Shape keys on the cube, in a step of their own."""
    cube = bpy.data.objects["Cube"]
    for name in names:
        cube.shape_key_add(name=name)


def shape_keys_from_the_step(names):
    """After an Undo to a step holding ``names`` on the cube: its keys back, and no Key without its owner's pointer."""
    keys = bpy.data.objects["Cube"].data.shape_keys
    return {"keys_from_the_step": keys is not None and [b.name for b in keys.key_blocks] == list(names),
            "no_orphaned_key": all(k.user is not None and getattr(k.user, "shape_keys", None) == k
                                   for k in bpy.data.shape_keys)}


def write_nested_library(outer, inner):
    """A library at ``outer`` whose "Crate" is parented to "Bolt", linked from a library at ``inner``."""
    write_library(inner, ("Bolt",))
    with bpy.data.libraries.load(str(inner), link=True) as (_source, target):
        target.objects = ["Bolt"]
    bolt = target.objects[0]
    crate = bpy.data.objects.new("Crate", None)
    crate.parent = bolt
    bpy.data.libraries.write(str(outer), {crate}, fake_user=True)
    bpy.data.objects.remove(crate)
    bpy.data.libraries.remove(bolt.library)
    return outer


def library_replaced(scene, out):
    """A generator: a library's object in a collection, in a step; then, with no step, the library removed (its objects
    go with it), its file linked again (a new Library) and the object linked into the scene; then Undo. The checks:
    the step's object is back in its collection. The object linked again with no step stays, as stock Blender keeps
    linked data an Undo did not record (``linked_from_a_library``)."""
    path = write_library(out / "undo-unrecorded-replaced.blend", ("Chair",))
    props = bpy.data.collections.new("Props")
    scene.collection.children.link(props)
    props_name = props.name   # Unique in the run: another case may have made a "Props".
    with bpy.data.libraries.load(str(path), link=True) as (_source, target):
        target.objects = ["Chair"]
    props.objects.link(target.objects[0])
    push("Chair linked")
    recorded_move()
    bpy.data.libraries.remove(bpy.data.objects["Chair"].library)
    link(path, "Chair", bpy.context.scene)
    press("ed.undo")
    for _ in range(8):
        yield 0.1
    chairs = [o for o in bpy.data.objects if o.name == "Chair" and o.library is not None]
    return {"chair_from_the_step": any(props_name in [c.name for c in chair.users_collection] for chair in chairs)}


def library_reloaded(scene, out, nested):
    """A generator: a library linked in a step, then reloaded with no step (its linked IDs get new session UIDs) and
    one of its objects linked into the scene with no step, then Undo. The checks to record."""
    if nested:
        path = write_nested_library(out / "undo-unrecorded-outer.blend", out / "undo-unrecorded-inner.blend")
        names = ("Crate",)
    else:
        path = write_library(out / "undo-unrecorded-reloaded.blend", ("Lamp", "Stool"))
        names = ("Lamp", "Stool")
    with bpy.data.libraries.load(str(path), link=True) as (_source, target):
        target.objects = list(names)
    shelf = bpy.data.collections.new("Shelf")
    scene.collection.children.link(shelf)
    shelf.objects.link(target.objects[0])
    shelf_name = shelf.name   # Unique in the run: the other variant made a "Shelf" too.
    push("Library linked")
    recorded_move()
    camera = bpy.data.objects["Camera"].as_pointer()   # Unchanged by every step: reused, its address kept.
    bpy.data.objects[names[0]].library.reload()
    bpy.context.scene.collection.objects.link(bpy.data.objects[names[-1]])
    press("ed.undo")
    for _ in range(8):
        yield 0.1
    first = bpy.data.objects.get(names[0])
    checks = {"no_whole_read": bpy.data.objects["Camera"].as_pointer() == camera,
              "linked_in_its_collection": first is not None and shelf_name in [c.name for c in first.users_collection]}
    if nested:
        checks["indirect_parent_kept"] = first is not None and first.parent is not None and first.parent.name == "Bolt"
    return checks


def build_heavy_scene(scene, count, extra, path):
    """A heavy scene for Undo timing (a generator, yielding to the UI): ``count`` objects sharing one mesh, each with a
    parent (``extra`` of them) and an ID property, plus ``extra`` objects linked from a library written to ``path``.
    Returns the collection of linked objects."""
    shared = bpy.data.meshes.new("Heavy Library Mesh")
    made = [bpy.data.objects.new("Heavy Linked %d" % index, shared) for index in range(extra)]
    bpy.data.libraries.write(str(path), set(made), fake_user=True)
    bpy.data.batch_remove(made + [shared])
    with bpy.data.libraries.load(str(path), link=True) as (_source, target):
        target.objects = ["Heavy Linked %d" % index for index in range(extra)]
    linked = bpy.data.collections.new("Heavy Linked")
    scene.collection.children.link(linked)
    for obj in target.objects:
        linked.objects.link(obj)
    yield 0.05
    mesh = bpy.data.meshes.new("Heavy")
    group = bpy.data.collections.new("Heavy")
    scene.collection.children.link(group)
    parents = [bpy.data.objects.new("Heavy Parent %d" % index, None) for index in range(extra)]
    for parent in parents:
        group.objects.link(parent)
    for index in range(count):
        obj = bpy.data.objects.new("Heavy %d" % index, mesh)
        obj.parent = parents[index % extra]
        obj["heavy_ref"] = mesh
        group.objects.link(obj)
        if index % 2000 == 1999:
            yield 0.05
    return linked


def consistent():
    """What a reused ID with a stale pointer breaks: the scene read as the depsgraph, the outliner and Python do."""
    problems = []
    for obj in bpy.data.objects:
        if obj.data is not None and obj.data.id_type != DATA_TYPE.get(obj.type, obj.type):
            problems.append("%s (%s) has %s data" % (obj.name, obj.type, obj.data.id_type))
        for modifier in obj.modifiers:
            target = getattr(modifier, "offset_object", None) or getattr(modifier, "object", None)
            if target is not None and target.name not in bpy.data.objects:
                problems.append("%s's %s points at %s, not in Main" % (obj.name, modifier.name, target.name))
    names = {obj.name for obj in bpy.data.objects}
    for collection in [bpy.context.scene.collection, *bpy.data.collections]:
        problems += ["%s lists %s, not in Main" % (collection.name, o.name)
                     for o in collection.objects if o.name not in names]
    problems += ["the view layer lists %s, not in Main" % o.name
                 for o in bpy.context.view_layer.objects if o.name not in names]
    bpy.context.evaluated_depsgraph_get().update()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)   # The outliner's draw included.
    return problems
