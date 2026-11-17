# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a person does by hand what one typed operation did: the reviewed hand routes (learn mode). No Blender here.

Angelo edits a scene with its own typed operations (``action_schemas.ACTION_SCHEMAS``); only ``operator_run`` names
a Blender operator. A how-to card (``learn_cards``) needs, for every other kind, the command a person would use.
This module is that table. Each entry holds **identifiers only**: an operator's idname, menu classes, RNA names.
The app looks the labels and the key up from itself when a card is drawn (``learn_resolver``), so a card follows a
rebound key, another keymap, another language and the next Blender release without an edit here, and no label or
shortcut written by a person can go stale.

Every kind is answered: ``hand(operation)`` returns the routes a person takes (one command each, in order), or a
reason there is no single hand command (``NO_ROUTE``). ``test_native_learn_routes`` fails on a kind with neither,
and proves each route's menus against Blender's own menu sources (``scripts/startup/bl_ui``): the menu classes
exist, each holds the next, and the last one holds the operator. ``test_blender_learn_routes`` proves the same in
the running app, with the keys.

A route says where the hand tool differs from what Angelo did (``differs``): Angelo works by number and index, a
person by mouse and click, and a card that hid that would teach a route nobody uses.

Text here is display text (``n_``), translated where a card is drawn; nothing in this module is read by a model.
"""

from dataclasses import dataclass

from .ui_text import n_

VIEW_3D, PROPERTIES, UV_EDITOR, NODE_EDITOR = "VIEW_3D", "PROPERTIES", "IMAGE_EDITOR", "NODE_EDITOR"
OBJECT, EDIT, POSE = "OBJECT", "EDIT", "POSE"
MODES = (OBJECT, EDIT, POSE)


@dataclass(frozen=True)
class Route:
    """One command a person runs, or one place they change a setting."""
    operator: str = ""       # The operator's idname ("" for a route that only changes settings).
    editor: str = VIEW_3D    # The editor whose menus and keys it is found in (a space type).
    mode: str = OBJECT       # The mode the person must be in.
    menus: tuple = ()        # Menu classes from the editor's header down to the one that holds the command.
    properties: tuple = ()   # ((RNA name, value), ...) that make the operator this action (type='BEVEL').
    choice: str = ""         # The RNA name in ``properties`` whose value is the menu item (an enum menu).
    item: str = ""           # The menu's own text for the command, as Blender's menu source writes it ("Bevel
                             # Edges"); "" when the menu shows the operator's label. Blender's message, never ours:
                             # the host test compares it with that source, and Blender's catalogue translates it.
    submenu: bool = False    # The choice's items sit in a submenu of their own, named by ``item`` or the label.
    settings: tuple = ()     # ((the operation's parameter, the operator's RNA name), ...) shown with their labels.
    tab: str = ""            # The Properties editor tab it lives in (``SpaceProperties.context``).
    title: str = ""          # n_: the step's title when there is no operator to take a label from.
    differs: str = ""        # n_: how doing it by hand differs from what Angelo did.

    @property
    def free(self):
        """The RNA names the person sets after the key: the menu's choice and the settings the card lists."""
        return tuple(name for name in (self.choice,) + tuple(rna for _, rna in self.settings) if name)


# How the hand tool differs. Whole sentences, one fact each.
BY_MOUSE = n_("By hand you start the tool and move the mouse; Angelo gave the distance as numbers.")
BY_CLICK = n_("By hand you click the parts to select them first; Angelo chose them by number.")
BY_CURSOR = n_("By hand the origin goes to the 3D cursor, so the cursor is placed at the point first.")
BONE_BY_BONE = n_("By hand you add one bone and extrude the others from it; Angelo placed them all at once.")
AUTO_WEIGHTS = n_("By hand the app works out the weights when you parent to the armature; Angelo wrote its own.")
NODE_BY_NODE = n_("By hand each node is added from this menu and linked by dragging between sockets; "
                  "Angelo built the graph in one step.")
SET_THEN_KEY = n_("By hand you go to the frame, set the value, then insert the key.")
IN_THE_STACK = n_("By hand this is done on the modifier's own panel, in its menu or by dragging its handle.")

# Why a kind has no single hand command.
OWN_TOOL = n_("Angelo did this with its own tool. No single command does the same by hand.")
IN_A_FIELD = n_("By hand this is typed or picked in a field of the editor, not run as a command.")
BY_DRAWING = n_("By hand this is drawn or placed point by point with the mouse.")
KEY_BY_KEY = n_("By hand this is done in an animation editor, key by key; Angelo wrote the result directly.")
AS_A_PATTERN = n_("Angelo repeated one step as a pattern. By hand it is done once, then duplicated or arrayed.")

# Titles of steps that change settings instead of running a command.
T_SETTINGS = n_("Change settings")
T_MODIFIER = n_("Change a modifier's settings")
T_CONSTRAINT = n_("Change a constraint's settings")
T_MATERIAL = n_("Change a material's settings")
T_OBJECT_DATA = n_("Change the object's data settings")
T_WORLD = n_("Change the world's settings")
T_BONE = n_("Change a bone's settings")
T_PARTICLES = n_("Change particle settings")
T_SHAPE_KEY = n_("Change a shape key's value")
T_NODES = n_("Build a node graph")

_ADD = ("VIEW3D_MT_add",)
_OBJECT = ("VIEW3D_MT_object",)
_MESH = ("VIEW3D_MT_edit_mesh",)

# A mesh primitive: its operator and how the operation's parameters are named on it.
_PRIMITIVES = {
    "CUBE": ("mesh.primitive_cube_add", "Cube", (("size", "size"),)),
    "PLANE": ("mesh.primitive_plane_add", "Plane", (("size", "size"),)),
    "UV_SPHERE": ("mesh.primitive_uv_sphere_add", "UV Sphere", (("radius", "radius"), ("segments", "segments"),   # i18n: Blender's menu text
                                                               ("rings", "ring_count"))),
    "ICO_SPHERE": ("mesh.primitive_ico_sphere_add", "Ico Sphere", (("radius", "radius"),   # i18n: Blender's menu text
                                                                 ("subdivisions", "subdivisions"))),
    "CYLINDER": ("mesh.primitive_cylinder_add", "Cylinder", (("radius", "radius"), ("depth", "depth"),
                                                            ("segments", "vertices"))),
    "CONE": ("mesh.primitive_cone_add", "Cone", (("radius", "radius1"), ("radius_top", "radius2"), ("depth", "depth"),
                                                 ("segments", "vertices"))),
    "GRID": ("mesh.primitive_grid_add", "Grid", (("size", "size"), ("x_segments", "x_subdivisions"),
                                                 ("y_segments", "y_subdivisions"))),
    "CIRCLE": ("mesh.primitive_circle_add", "Circle", (("radius", "radius"), ("segments", "vertices"))),
    "TORUS": ("mesh.primitive_torus_add", "Torus", (("major_radius", "major_radius"), ("minor_radius", "minor_radius"),
                                                   ("major_segments", "major_segments"),
                                                   ("minor_segments", "minor_segments"))),
}
_EMPTIES = {"PLAIN_AXES": "Plain Axes", "ARROWS": "Arrows", "SINGLE_ARROW": "Single Arrow", "CIRCLE": "Circle",   # i18n: Blender's menu text
            "CUBE": "Cube", "SPHERE": "Sphere", "CONE": "Cone"}
_UNWRAP_ITEMS = {"ANGLE_BASED": "Unwrap Angle Based", "CONFORMAL": "Unwrap Conformal"}   # i18n: Blender's menu text
# Apply's own entries for the channels it applies (location, rotation, scale); another mix has none.
_APPLY_ITEMS = {(True, False, False): "Location", (False, True, False): "Rotation", (False, False, True): "Scale",
                (True, True, True): "All Transforms", (False, True, True): "Rotation & Scale"}   # i18n: Blender's menu text

# The Add Modifier menu's page for each modifier type, as Blender's own menu source has it
# (``properties_data_modifier.py``; the host test compares this table with that source).
_MODIFIER_PAGES = {
    "edit": ("DATA_TRANSFER", "GREASE_PENCIL_TEXTURE", "GREASE_PENCIL_TIME",
        "GREASE_PENCIL_VERTEX_WEIGHT_ANGLE", "GREASE_PENCIL_VERTEX_WEIGHT_PROXIMITY", "MESH_CACHE",
        "MESH_SEQUENCE_CACHE", "TENSION", "UV_PROJECT", "UV_WARP", "VERTEX_WEIGHT_EDIT",
        "VERTEX_WEIGHT_MIX", "VERTEX_WEIGHT_PROXIMITY"),
    "generate": ("ARRAY", "BEVEL", "BOOLEAN", "BUILD", "DECIMATE", "EDGE_SPLIT", "GREASE_PENCIL_ARRAY",
        "GREASE_PENCIL_BUILD", "GREASE_PENCIL_DASH", "GREASE_PENCIL_ENVELOPE", "GREASE_PENCIL_LENGTH",
        "GREASE_PENCIL_MIRROR", "GREASE_PENCIL_MULTIPLY", "GREASE_PENCIL_OUTLINE",
        "GREASE_PENCIL_SIMPLIFY", "GREASE_PENCIL_SUBDIV", "LINEART", "MASK", "MESH_TO_VOLUME", "MIRROR",
        "MULTIRES", "REMESH", "SCREW", "SKIN", "SOLIDIFY", "SUBSURF", "TRIANGULATE", "VOLUME_TO_MESH",
        "WELD", "WIREFRAME"),
    "deform": ("ARMATURE", "CAST", "CORRECTIVE_SMOOTH", "CURVE", "DISPLACE", "GREASE_PENCIL_ARMATURE",
        "GREASE_PENCIL_HOOK", "GREASE_PENCIL_LATTICE", "GREASE_PENCIL_NOISE", "GREASE_PENCIL_OFFSET",
        "GREASE_PENCIL_SHRINKWRAP", "GREASE_PENCIL_SMOOTH", "GREASE_PENCIL_THICKNESS", "HOOK",
        "LAPLACIANDEFORM", "LAPLACIANSMOOTH", "LATTICE", "MESH_DEFORM", "SHRINKWRAP", "SIMPLE_DEFORM",
        "SMOOTH", "SURFACE_DEFORM", "VOLUME_DISPLACE", "WARP", "WAVE"),
    "normals": ("NORMAL_EDIT", "WEIGHTED_NORMAL"),
    "physics": ("CLOTH", "COLLISION", "DYNAMIC_PAINT", "EXPLODE", "FLUID", "OCEAN", "PARTICLE_INSTANCE",
        "PARTICLE_SYSTEM", "SOFT_BODY"),
    "color": ("GREASE_PENCIL_COLOR", "GREASE_PENCIL_OPACITY", "GREASE_PENCIL_TINT"),
}
MODIFIER_MENU = "OBJECT_MT_modifier_add"
MODIFIER_PAGE = {kind: MODIFIER_MENU + "_" + page for page, kinds in _MODIFIER_PAGES.items() for kind in kinds}

_LIGHTS = ("POINT", "SUN", "SPOT", "AREA")
_SEPARATE = {"SELECTION": "SELECTED", "LOOSE": "LOOSE", "MATERIAL": "MATERIAL"}
_DELETE = {"FACES": "FACE", "FACES_ONLY": "ONLY_FACE"}
_ORIGIN = {"BOUNDS_CENTER": (("type", "ORIGIN_GEOMETRY"), ("center", "BOUNDS")),
           "MEDIAN": (("type", "ORIGIN_GEOMETRY"), ("center", "MEDIAN")),
           "POINT": (("type", "ORIGIN_CURSOR"),)}
_UV_PROJECT = {"CUBE": "uv.cube_project", "SPHERICAL": "uv.sphere_project"}
_UVS = ("IMAGE_MT_uvs",)
_UNWRAP = _UVS + ("IMAGE_MT_uvs_unwrap",)


def _create_primitive(p):
    operator, item, settings = _PRIMITIVES[p["primitive"]]
    return (Route(operator, menus=_ADD + ("VIEW3D_MT_mesh_add",), item=item, settings=settings),)


def _create_object(p):
    kind = p["type"]
    if kind == "EMPTY":
        display = p.get("display", "PLAIN_AXES")
        return (Route("object.empty_add", menus=_ADD + ("VIEW3D_MT_empty_add",), properties=(("type", display),),
                      item=_EMPTIES[display]),)
    if kind == "TEXT":
        return (Route("object.text_add", menus=_ADD, item="Text"),)
    if kind == "CURVE":
        if p.get("shape") == "CIRCLE":
            return (Route("curve.primitive_bezier_circle_add", menus=_ADD + ("VIEW3D_MT_curve_add",), item="Circle"),)
        return (Route("curve.primitive_bezier_curve_add", menus=_ADD + ("VIEW3D_MT_curve_add",), item="Bézier"),)
    return ()   # A lattice or Grease Pencil object: Angelo's own set-up (OWN_TOOL).


def _transform(p):
    """Move, rotate and scale are three commands by hand; only those the operation states."""
    found = []
    for field, operator, item in (("location", "transform.translate", ""), ("rotation", "transform.rotate", ""),
                                  ("scale", "transform.resize", "Scale")):
        if p.get(field) is not None:
            found.append(Route(operator, menus=_OBJECT + ("VIEW3D_MT_transform_object",), item=item, differs=BY_MOUSE))
    return tuple(found)


def _modifier_add(p):
    kind = p["type"]
    page = MODIFIER_PAGE.get(kind)
    return (Route("object.modifier_add", PROPERTIES, tab="MODIFIER",
                  menus=(MODIFIER_MENU,) + ((page,) if page else ()), properties=(("type", kind),), choice="type"),)


def _parent_set(p):
    if p.get("parent") is None:
        kind = "CLEAR_KEEP_TRANSFORM" if p.get("keep_transform", True) else "CLEAR"
        return (Route("object.parent_clear", menus=_OBJECT + ("VIEW3D_MT_object_parent",),
                      properties=(("type", kind),), choice="type"),)
    return (Route("object.parent_set", menus=_OBJECT + ("VIEW3D_MT_object_parent",),
                  properties=(("type", "OBJECT"),), choice="type"),)


def _transform_apply(p):
    chosen = (bool(p.get("location")), bool(p.get("rotation")), bool(p.get("scale")))
    item = _APPLY_ITEMS.get(chosen)
    if item:
        return (Route("object.transform_apply", menus=_OBJECT + ("VIEW3D_MT_object_apply",), item=item),)
    if not any(chosen):
        return ()
    # No entry applies just these (Location and Scale): the entry for the first, then the others are ticked in
    # Adjust Last Operation, which the card's settings list.
    first = next(name for name, on in zip(("Location", "Rotation", "Scale"), chosen) if on)   # i18n: Blender's menu text
    return (Route("object.transform_apply", menus=_OBJECT + ("VIEW3D_MT_object_apply",), item=first,
                  settings=(("location", "location"), ("rotation", "rotation"), ("scale", "scale"))),)


def _duplicate(p):
    operator = "object.duplicate_move_linked" if p.get("linked") else "object.duplicate_move"
    return (Route(operator, menus=_OBJECT, differs=BY_MOUSE),)


def _visibility(p):
    if p.get("collection") is not None or p.get("hidden") is None:
        return ()   # A collection's flags are ticked in the Outliner (IN_A_FIELD).
    if p["hidden"]:
        return (Route("object.hide_view_set", menus=_OBJECT + ("VIEW3D_MT_object_showhide",),
                      properties=(("unselected", False),), item="Hide Selected"),)   # i18n: Blender's menu text
    return (Route("object.hide_view_clear", menus=_OBJECT + ("VIEW3D_MT_object_showhide",)),)


def _collection(p):
    if p.get("mode") == "UNLINK":
        return ()
    operator = "object.move_to_collection" if p.get("mode") == "MOVE" else "object.link_to_collection"
    return (Route(operator),)


def _mesh_delete(p):
    kind = _DELETE.get(p.get("mode", "FACES"))
    if kind is None:   # Keeping the region's boundary edges is no entry of the Delete menu: Angelo's own tool.
        return ()
    return (Route("mesh.delete", mode=EDIT, menus=_MESH + ("VIEW3D_MT_edit_mesh_delete",),
                  properties=(("type", kind),), choice="type", differs=BY_CLICK),)


def _mesh_separate(p):
    return (Route("mesh.separate", mode=EDIT, menus=_MESH,
                  properties=(("type", _SEPARATE[p.get("mode", "LOOSE")]),), choice="type", submenu=True),)


def _origin(p):
    mode = p.get("mode", "BOUNDS_CENTER")
    return (Route("object.origin_set", menus=_OBJECT, properties=_ORIGIN[mode], choice="type", item="Set Origin",   # i18n: Blender's menu text
                  submenu=True, differs=BY_CURSOR if mode == "POINT" else ""),)


def _uv_unwrap(p):
    return (Route("uv.unwrap", UV_EDITOR, EDIT, menus=_UNWRAP, item=_UNWRAP_ITEMS[p.get("method", "ANGLE_BASED")]),)


def _uv_project(p):
    operator = _UV_PROJECT.get(p.get("method", "CUBE"))
    return (Route(operator, UV_EDITOR, EDIT, menus=_UNWRAP),) if operator else ()


def _light(p):
    kind = (p.get("settings") or {}).get("light_type", "POINT")
    return (Route("object.light_add", menus=_ADD + ("VIEW3D_MT_light_add",),
                  properties=(("type", kind),) if kind in _LIGHTS else (), choice="type" if kind in _LIGHTS else ""),)


def _material_assign(p):
    if p.get("face_indices"):
        return (Route("object.material_slot_assign", PROPERTIES, EDIT, tab="MATERIAL", differs=BY_CLICK),)
    return ()


def _operator_run(p):
    from . import action_operator_spec as spec
    operator = p["operator"]
    module = operator.split(".", 1)[0]
    if module in spec.POSE_MODULES:
        mode = POSE
    else:   # The kernel's own rule (action_operator_modes.modes): Edit modules and strokes in Edit Mode.
        mode = EDIT if spec.edit_only(operator) else OBJECT
    given = p.get("properties") if isinstance(p.get("properties"), dict) else {}
    # The record's own properties are what make the operator this action: a key bound to the same operator with
    # another value (vertex select mode for an edge one, delete vertices for delete faces) is not its key.
    properties = tuple((name, tuple(value) if isinstance(value, list) else value) for name, value in given.items())
    names = tuple((name, name) for name in given)
    menus, item = _header_entry(operator, dict(properties))
    return (Route(operator, UV_EDITOR if module == "uv" else VIEW_3D, mode, menus=menus, properties=properties,
                  item=item, settings=names),)


def _header_entry(operator, properties):
    """(menus, item) of the header menu entry that runs ``operator`` as recorded, or ((), ""): Blender's search finds
    a command only through the header's menus, under their text (``learn_menu_paths``). An entry that sets a property
    is this action only when the record holds that value; the one setting the most of the record's wins, then the
    shortest path."""
    from .learn_menu_paths import PATHS
    best = None
    for menus, item, fixed in PATHS.get(operator, ()):
        if all(name in properties and properties[name] == value for name, value in fixed):
            if best is None or len(fixed) > len(best[2]):
                best = (menus, item, fixed)
    return best[:2] if best else ((), "")


def _settings(title, tab):
    return lambda p: (Route(editor=PROPERTIES, tab=tab, title=title),)


def _one(*args, **kwargs):
    route = Route(*args, **kwargs)
    return lambda p: (route,)


# kind -> the routes of one operation's parameters. A kind whose function returns none falls to NO_ROUTE.
ROUTES = {
    "create_primitive": _create_primitive,
    "create_object": _create_object,
    "camera_create": _one("object.camera_add", menus=_ADD + ("VIEW3D_MT_camera_add",), item="Camera"),
    "light_create": _light,
    "armature_create": _one("object.armature_add", menus=_ADD, item="Armature", differs=BONE_BY_BONE),
    "transform": _transform,
    "transform_apply": _transform_apply,
    "origin_set": _origin,
    "parent_set": _parent_set,
    "object_duplicate": _duplicate,
    "object_delete": _one("object.delete", menus=_OBJECT, properties=(("use_global", False),), item="Delete"),
    "object_join": _one("object.join", menus=_OBJECT),
    "object_convert": _one("object.convert", menus=_OBJECT + ("VIEW3D_MT_object_convert",),
                           properties=(("target", "MESH"),), choice="target"),
    "collection_link": _collection,
    "visibility_set": _visibility,
    "mesh_extrude": _one("view3d.edit_mesh_extrude_move_normal", mode=EDIT, menus=("VIEW3D_MT_edit_mesh_faces",),
                         item="Extrude Faces", differs=BY_MOUSE),   # i18n: Blender's menu text
    "mesh_inset": _one("mesh.inset", mode=EDIT, menus=("VIEW3D_MT_edit_mesh_faces",),
                       settings=(("thickness", "thickness"), ("depth", "depth"), ("individual", "use_individual"),
                                 ("use_even_offset", "use_even_offset"))),
    "mesh_bevel": _one("mesh.bevel", mode=EDIT, menus=("VIEW3D_MT_edit_mesh_edges",),
                       properties=(("affect", "EDGES"),), item="Bevel Edges",   # i18n: Blender's menu text
                       settings=(("offset", "offset"), ("segments", "segments"), ("profile", "profile"),
                                 ("clamp_overlap", "clamp_overlap"))),
    "mesh_subdivide": _one("mesh.subdivide", mode=EDIT, menus=("VIEW3D_MT_edit_mesh_edges",),
                           settings=(("cuts", "number_cuts"), ("smoothness", "smoothness"))),
    "mesh_merge_by_distance": _one("mesh.remove_doubles", mode=EDIT, menus=_MESH + ("VIEW3D_MT_edit_mesh_merge",),
                                   item="By Distance", settings=(("distance", "threshold"),)),   # i18n: Blender's menu text
    "mesh_delete": _mesh_delete,
    "mesh_recalculate_normals": _one("mesh.normals_make_consistent", mode=EDIT,
                                     menus=_MESH + ("VIEW3D_MT_edit_mesh_normals",), properties=(("inside", False),),
                                     item="Recalculate Outside"),   # i18n: Blender's menu text
    "mesh_separate": _mesh_separate,
    "modifier_add": _modifier_add,
    "modifier_configure": _settings(T_MODIFIER, "MODIFIER"),
    "modifier_apply": _one("object.modifier_apply", PROPERTIES, tab="MODIFIER", differs=IN_THE_STACK),
    "modifier_remove": _one("object.modifier_remove", PROPERTIES, tab="MODIFIER", differs=IN_THE_STACK),
    "modifier_move": _one("object.modifier_move_to_index", PROPERTIES, tab="MODIFIER", differs=IN_THE_STACK),
    "material_principled": _one("material.new", PROPERTIES, tab="MATERIAL"),
    "material_assign": _material_assign,
    "shader_nodes": _one(editor=NODE_EDITOR, menus=("NODE_MT_add",), title=T_NODES, differs=NODE_BY_NODE),
    "geometry_nodes": _one(editor=NODE_EDITOR, menus=("NODE_MT_add",), title=T_NODES, differs=NODE_BY_NODE),
    "node_edit": _one(editor=NODE_EDITOR, menus=("NODE_MT_add",), title=T_NODES, differs=NODE_BY_NODE),
    "uv_project": _uv_project,
    "uv_unwrap": _uv_unwrap,
    "uv_smart_project": _one("uv.smart_project", UV_EDITOR, EDIT, menus=_UNWRAP, item="Smart UV Project...",   # i18n: Blender's menu text
                             settings=(("angle_limit", "angle_limit"), ("island_margin", "island_margin"),
                                       ("area_weight", "area_weight"), ("correct_aspect", "correct_aspect"),
                                       ("scale_to_bounds", "scale_to_bounds"))),
    "uv_pack": _one("uv.pack_islands", UV_EDITOR, EDIT, menus=_UVS,
                    settings=(("margin", "margin"), ("rotate", "rotate"))),
    "constraint_add": lambda p: (Route("object.constraint_add", PROPERTIES, tab="CONSTRAINT",
                                       properties=(("type", p["type"]),), choice="type"),),
    "constraint_configure": _settings(T_CONSTRAINT, "CONSTRAINT"),
    "keyframe_insert": _one("anim.keyframe_insert", menus=_OBJECT + ("VIEW3D_MT_object_animation",),
                            item="Insert Keyframe", differs=SET_THEN_KEY),   # i18n: Blender's menu text
    "keyframe_delete": _one("anim.keyframe_delete_v3d", menus=_OBJECT + ("VIEW3D_MT_object_animation",),
                            item="Delete Keyframes..."),   # i18n: Blender's menu text
    "property_keyframe": _one("anim.keyframe_insert_button", PROPERTIES, differs=SET_THEN_KEY),
    "armature_bind": _one("object.parent_set", menus=_OBJECT + ("VIEW3D_MT_object_parent",),
                          properties=(("type", "ARMATURE_AUTO"),), choice="type", differs=AUTO_WEIGHTS),
    "shape_key_add": _one("object.shape_key_add", PROPERTIES, tab="DATA"),
    "shape_key_value": _settings(T_SHAPE_KEY, "DATA"),
    "property_set": _settings(T_SETTINGS, ""),
    "camera_configure": _settings(T_OBJECT_DATA, "DATA"),
    "light_configure": _settings(T_OBJECT_DATA, "DATA"),
    "world_configure": _settings(T_WORLD, "WORLD"),
    "bone_set": _settings(T_BONE, "BONE"),
    "particle_set": _settings(T_PARTICLES, "PARTICLES"),
    "operator_run": _operator_run,
}

# kind -> why there is no single hand command. Also the answer for a ROUTES kind whose parameters have none.
NO_ROUTE = {
    "mesh_bend": OWN_TOOL,
    "create_object": OWN_TOOL,
    "uv_project": OWN_TOOL,
    "collection_link": IN_A_FIELD,
    "visibility_set": IN_A_FIELD,
    "material_assign": IN_A_FIELD,
    "object_rename": IN_A_FIELD,
    "action_set": IN_A_FIELD,
    "curve_points": BY_DRAWING,
    "keyframe_edit": KEY_BY_KEY,
    "keyframe_copy": KEY_BY_KEY,
    "keyframe_configure": KEY_BY_KEY,
    "fcurve_modifier": KEY_BY_KEY,
    "marker_edit": KEY_BY_KEY,
    "nla_edit": KEY_BY_KEY,
    "nla_push_down": KEY_BY_KEY,
    "batch": AS_A_PATTERN,
    "transform": IN_A_FIELD,   # A transform that states no channel.
}


@dataclass(frozen=True)
class Hand:
    routes: tuple = ()   # The commands a person runs, in order.
    reason: str = ""     # n_: why there is none, when ``routes`` is empty.


def hand(operation):
    """How a person does ``operation`` (a canonical typed operation) by hand: its routes, or the reason there is no
    single command. An unknown kind (a later build's) is Angelo's own tool, never a guess."""
    kind = operation.get("kind") if isinstance(operation, dict) else None
    parameters = operation.get("parameters") if isinstance(operation, dict) else None
    build = ROUTES.get(kind)
    if build is not None and isinstance(parameters, dict):
        try:
            routes = tuple(build(parameters))
        except (KeyError, TypeError, AttributeError):   # A record another build wrote, missing a field.
            routes = ()
        if routes:
            return Hand(routes)
    return Hand(reason=NO_ROUTE.get(kind, OWN_TOOL))


def answered():
    """Every kind this table answers, with a route or a reason."""
    return frozenset(ROUTES) | frozenset(NO_ROUTE)
