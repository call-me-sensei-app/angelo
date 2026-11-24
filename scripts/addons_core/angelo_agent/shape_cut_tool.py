# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shape Cut by hand: draw a shape on a surface, set its depth, get a live cut (doc/angelo/shape_cut.rst).

One modal operator in the 3D viewport, Object or Edit mode. Hover shows the surface's frame and grid; drag draws a box
or circle (click corners for a polygon, hold and draw for a freehand outline); moving the mouse then sets the depth
while the real Boolean shows the result; click commits. Everything it makes goes through ``shape_cut.cut``, the
same path as the agent's ``shape_cut`` operation. Keys (shown in the status bar, translated): 1-6 operation, B C P F
shape, Ctrl grid snap on/off, Shift square, Alt from centre, T through, Esc or right click cancels and leaves nothing.
Reached from the chat bar's + menu and F3. The interaction is Blender's own Add Cube tool's (surface-aligned base,
then depth), not any other product's.
"""

import math

import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader

from . import input_policy, shape_cut, shape_cut_geometry as geo, shape_cut_spec as spec_mod, ui_tokens
from .ui_text import iface_, n_

TITLE = n_("Shape Cut")
WHY = n_("Draw a shape on a surface to cut, add or slice it, live")
SHAPES = (("BOX", n_("Box"), ""), ("CIRCLE", n_("Circle"), ""), ("POLYGON", n_("Polygon"), ""),
          ("FREEHAND", n_("Freehand"), ""))
OPERATION_LABELS = {"CUT": n_("Cut"), "UNION": n_("Union"), "INTERSECT": n_("Intersect"), "SLICE": n_("Slice"),
                    "INSET": n_("Inset"), "PANEL": n_("Panel")}
OPERATIONS = tuple((o, OPERATION_LABELS[o], "") for o in spec_mod.OPERATIONS)
SHAPE_KEYS = {"B": "BOX", "C": "CIRCLE", "P": "POLYGON", "F": "FREEHAND"}
OPERATION_KEYS = {key: op for key, op in zip(("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX"), spec_mod.OPERATIONS)}
LIVE_PREVIEW_FACES = 200_000   # Above this many target faces, depth moves the outline; the Boolean runs on commit.
CLOSE_PIXELS = 12
GRID_CELLS = 8


def _ray(context, event):
    region, view = context.region, context.region_data
    xy = (event.mouse_region_x, event.mouse_region_y)
    return (tuple(view3d_utils.region_2d_to_origin_3d(region, view, xy)),
            tuple(view3d_utils.region_2d_to_vector_3d(region, view, xy)))


def _hit(context, origin, direction):
    """The first non-cutter mesh surface under the mouse: (object, face index, location) or None."""
    graph = context.evaluated_depsgraph_get()
    start = origin
    for _ in range(16):   # Step past cutters (wire, but still in the scene).
        found, location, _normal, index, obj, _matrix = context.scene.ray_cast(graph, start, direction)
        if not found:
            return None
        if obj.type == "MESH" and not obj.get(shape_cut.TAG):
            return obj, index, tuple(location)
        start = tuple(location[i] + direction[i] * 1e-5 for i in range(3))
    return None


class ANGELO_OT_shape_cut(bpy.types.Operator):
    bl_idname = "angelo.shape_cut"
    bl_label = TITLE
    bl_description = WHY
    bl_options = {"UNDO"}   # No redo panel: a drawn outline is not in the properties, so redo could not repeat it.

    shape: bpy.props.EnumProperty(name="Shape", items=SHAPES, default="BOX")
    operation: bpy.props.EnumProperty(name="Operation", items=OPERATIONS, default="CUT")
    width: bpy.props.FloatProperty(name="Width", description="An inset's wall or a panel line's width",
                                   default=0.005, min=1e-5, max=10.0, unit="LENGTH")
    align: bpy.props.EnumProperty(name="Align", items=(("EDGE", n_("Edge"), ""), ("WORLD", n_("World"), "")),
                                  default="EDGE")

    # Run without drawing (EXEC_DEFAULT: the agent's shape_cut operation, scripts): a cut on the active mesh's face.
    face: bpy.props.IntProperty(name="Face", default=-1, min=-1,
                                description="Face index on the evaluated mesh; -1 uses Towards")
    towards: bpy.props.FloatVectorProperty(name="Towards", size=3, default=(0.0, 0.0, 1.0),
                                           description="The largest face within 30 degrees of this direction")
    size: bpy.props.FloatVectorProperty(name="Size", size=2, default=(0.1, 0.1), min=1e-5, max=1000.0,
                                        unit="LENGTH")
    radius: bpy.props.FloatProperty(name="Radius", default=0.05, min=1e-5, max=1000.0, unit="LENGTH")
    segments: bpy.props.IntProperty(name="Segments", default=32, min=3, max=256)
    offset: bpy.props.FloatVectorProperty(name="Offset", size=2, default=(0.0, 0.0), min=-1000.0, max=1000.0,
                                          unit="LENGTH", description="From the face's centre, u along its longest edge")
    rotation: bpy.props.FloatProperty(name="Rotation", default=0.0, subtype="ANGLE")
    depth: bpy.props.FloatProperty(name="Depth", default=0.01, min=1e-5, max=1000.0, unit="LENGTH",
                                   description="Below the surface; a Union's height above it")
    through: bpy.props.BoolProperty(name="Through", default=False)
    columns: bpy.props.IntProperty(name="Columns", default=1, min=1, max=spec_mod.MAX_COPIES)
    rows: bpy.props.IntProperty(name="Rows", default=1, min=1, max=spec_mod.MAX_COPIES)
    spacing: bpy.props.FloatVectorProperty(name="Spacing", size=2, default=(0.0, 0.0), min=0.0, max=1000.0,
                                           unit="LENGTH")

    @classmethod
    def poll(cls, context):
        # Run from its properties (EXEC_DEFAULT: the agent, a script, a background worker) it needs no viewport;
        # drawing does, and invoke says so.
        return context.mode in {"OBJECT", "EDIT_MESH"}

    def execute(self, context):
        """The cut these properties describe, on the active mesh, in Object Mode; no drawing."""
        target = context.active_object
        if target is None or target.type != "MESH" or target.mode != "OBJECT":
            self.report({"ERROR"}, iface_("Shape Cut needs an active mesh in Object Mode"))
            return {"CANCELLED"}
        if self.shape not in {"BOX", "CIRCLE"}:
            self.report({"ERROR"}, iface_("Without drawing, Shape Cut makes a box or a circle"))
            return {"CANCELLED"}
        graph = context.evaluated_depsgraph_get()
        try:
            index = self.face if self.face >= 0 else shape_cut.face_towards(target, graph, tuple(self.towards))
            frame, centre = shape_cut.face_frame(target, graph, index, align=self.align)
            cut = {"shape": self.shape, "operation": self.operation, "rotation": math.degrees(self.rotation),
                   "center": [centre[0] + self.offset[0], centre[1] + self.offset[1]],
                   "depth": spec_mod.THROUGH if self.through else self.depth}
            if self.shape == "BOX":
                cut["size"] = list(self.size)
            else:
                cut.update(radius=self.radius, segments=self.segments)
            if self.operation in {"INSET", "PANEL"}:
                cut["width"] = self.width
            if self.columns * self.rows > 1:
                cut["array"] = {"columns": self.columns, "rows": self.rows, "spacing": list(self.spacing)}
            made = shape_cut.cut(context.scene, graph, target, frame, cut,
                                 name=iface_(OPERATION_LABELS[self.operation]))
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("{operation} made with the {solver} solver",
                                     operation=iface_(OPERATION_LABELS[self.operation]),
                                     solver=made["solver"].title()))
        return {"FINISHED"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"ERROR"}, iface_("Draw a Shape Cut in a 3D Viewport"))
            return {"CANCELLED"}
        self.state, self.frame, self.target, self.points = "HOVER", None, None, []
        self.anchor = self.current = None
        self.depth, self.through, self.made, self.outline = 0.0, False, None, None
        self.snap_flip = self.square = self.centred = False
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (context,), "WINDOW", "POST_VIEW")
        context.window_manager.modal_handler_add(self)
        self._status(context)
        return {"RUNNING_MODAL"}

    def _grid(self, context):
        size = context.space_data.overlay.grid_scale * context.scene.unit_settings.scale_length
        return 0.0 if self.snap_flip else size * 0.1   # Snap on by default, at a tenth of the floor grid.

    def _local(self, context, event):
        origin, direction = _ray(context, event)
        point = geo.ray_plane(self.frame, origin, direction)
        return None if point is None else geo.snap(point, self._grid(context))

    def _status(self, context):
        context.workspace.status_text_set(iface_(
            "{shape} {operation}: drag on a surface | 1-6 operation | B C P F shape | Ctrl grid | T through | "
            "Esc cancel", shape=iface_(dict((s[0], s[1]) for s in SHAPES)[self.shape]),
            operation=iface_(OPERATION_LABELS[self.operation])))

    def modal(self, context, event):
        if input_policy.overlay_passes(event, context.window_manager):
            return {"PASS_THROUGH"}   # Timers and window events are not the person's drawing; Ctrl+Q still quits.
        context.area.tag_redraw()
        self.square, self.centred = event.shift, event.alt
        if event.type in {"LEFT_CTRL", "RIGHT_CTRL"} and event.value == "PRESS":
            self.snap_flip = not self.snap_flip
        if event.value == "PRESS" and event.type in OPERATION_KEYS and self.state != "DEPTH":
            self.operation = OPERATION_KEYS[event.type]
        if event.value == "PRESS" and event.type in SHAPE_KEYS and self.state == "HOVER":
            self.shape = SHAPE_KEYS[event.type]
        if event.value == "PRESS" and event.type == "T" and self.operation in {"CUT", "INTERSECT", "SLICE"}:
            self.through = not self.through
        self._status(context)
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._cancel(context)
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM"}:
            return {"PASS_THROUGH"}   # The view still turns and zooms while drawing.
        handler = {"HOVER": self._hover, "DRAW": self._drawing, "DEPTH": self._depth}[self.state]
        return handler(context, event)

    def _hover(self, context, event):
        origin, direction = _ray(context, event)
        hit = _hit(context, origin, direction)
        if hit is None:
            self.frame = None
            return {"RUNNING_MODAL"}
        obj, index, location = hit
        self.target = obj
        self.frame, _centre = shape_cut.face_frame(obj, context.evaluated_depsgraph_get(), index, location,
                                                   self.align)
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            self.anchor = self.current = geo.snap(self.frame.to_local(location)[:2], self._grid(context))
            self.points = [self.anchor]
            self.state = "DRAW"
        return {"RUNNING_MODAL"}

    def _drawing(self, context, event):
        point = self._local(context, event)
        if point is None:
            return {"RUNNING_MODAL"}
        self.current = point
        if self.shape == "FREEHAND" and event.type == "MOUSEMOVE":
            self.points.append(point)
        pixel = self._pixel_size(context)
        if self.shape == "POLYGON":
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                if geo.closes(self.points, point, CLOSE_PIXELS * pixel):
                    return self._to_depth(context, event)
                self.points.append(point)
            elif event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS" and len(self.points) >= 3:
                return self._to_depth(context, event)
        elif event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self._to_depth(context, event)
        return {"RUNNING_MODAL"}

    def _pixel_size(self, context):
        """World metres per pixel at the frame's origin, for a click tolerance that feels the same at any zoom."""
        region, view = context.region, context.region_data
        a = view3d_utils.location_3d_to_region_2d(region, view, self.frame.origin)
        b = view3d_utils.location_3d_to_region_2d(region, view, self.frame.to_world(1.0, 0.0))
        return 1.0 / max((b - a).length, 1e-6) if a is not None and b is not None else 0.01

    def _drawn_outline(self):
        if self.shape == "BOX":
            return geo.box(self.anchor, self.current, square=self.square, centered=self.centred)
        if self.shape == "CIRCLE":
            radius = ((self.current[0] - self.anchor[0]) ** 2 + (self.current[1] - self.anchor[1]) ** 2) ** 0.5
            return geo.circle(self.anchor, radius, 48)
        if self.shape == "FREEHAND":
            return geo.simplify(self.points, max(self._grid_or_size() * 0.02, 1e-5))
        return geo.polygon(self.points)

    def _grid_or_size(self):
        xs, ys = [p[0] for p in self.points], [p[1] for p in self.points]
        return max(max(xs) - min(xs), max(ys) - min(ys), 1e-4)

    def _spec(self):
        centre = (sum(p[0] for p in self.outline) / len(self.outline), sum(p[1] for p in self.outline) /
                  len(self.outline))
        cut = {"shape": "POLYGON", "points": [[x - centre[0], y - centre[1]] for x, y in self.outline],
               "center": list(centre), "operation": self.operation,
               "depth": "THROUGH" if self.through else max(self.depth, 1e-4)}
        if self.operation in {"INSET", "PANEL"}:
            cut["width"] = self.width
        return cut

    def _to_depth(self, context, event):
        try:
            self.outline = self._drawn_outline()
        except ValueError as error:   # A click, a crossing stroke: say why and let them draw again.
            self.report({"WARNING"}, str(error))
            self.state, self.points = "HOVER", []
            return {"RUNNING_MODAL"}
        self.state = "DEPTH"
        self.depth = spec_mod.size_of(self.outline) * 0.25
        self._rebuild(context, force=True)
        return {"RUNNING_MODAL"}

    def _rebuild(self, context, force=False):
        """The live cut: made once, then its cutters' vertices follow the depth in place (a depth or THROUGH change
        never changes a solid's topology). Above LIVE_PREVIEW_FACES only a forced update (commit) moves them."""
        if not force and len(self.target.data.polygons) > LIVE_PREVIEW_FACES:
            return
        if self.made is not None:
            graph = context.evaluated_depsgraph_get()
            cut = spec_mod.validate(self._spec())
            through = spec_mod.through_depth(self.frame, shape_cut.world_corners(self.target, graph)) \
                if cut["depth"] == spec_mod.THROUGH else None
            for name, solid in zip(self.made["cutters"], spec_mod.solids(cut, through)):
                mesh = bpy.data.objects[name].data
                mesh.vertices.foreach_set("co", [c for vert in solid["verts"] for c in vert])
                mesh.update()
            return
        try:
            self.made = shape_cut.cut(context.scene, context.evaluated_depsgraph_get(), self.target, self.frame,
                                      self._spec(), name=iface_(OPERATION_LABELS[self.operation]))
        except ValueError as error:
            self.report({"WARNING"}, str(error))

    def _depth(self, context, event):
        if event.type == "MOUSEMOVE":
            origin, direction = _ray(context, event)
            centre = (sum(p[0] for p in self.outline) / len(self.outline),
                      sum(p[1] for p in self.outline) / len(self.outline))
            depth = geo.depth_along_normal(self.frame, centre, origin, direction)
            grid = self._grid(context)
            self.depth = max(geo.snap((depth, 0), grid)[0] if grid else depth, 1e-4)
            self._rebuild(context)
        elif event.type == "T" and event.value == "PRESS":
            self._rebuild(context, force=True)
        elif (event.type == "LEFTMOUSE" and event.value == "PRESS") or (
                event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS"):
            self._rebuild(context, force=True)
            self._end(context)
            if self.made is None:
                return {"CANCELLED"}
            self.report({"INFO"}, iface_("{operation} made with the {solver} solver",
                                         operation=iface_(OPERATION_LABELS[self.operation]),
                                         solver=self.made["solver"].title()))
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _cancel(self, context):
        if self.made is not None:
            for name in self.made["cutters"]:
                shape_cut.remove_cutter(bpy.data.objects[name])
            if self.made["piece"]:
                bpy.data.objects.remove(bpy.data.objects[self.made["piece"]])
        self._end(context)
        return {"CANCELLED"}

    def _end(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, "WINDOW")
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def _draw(self, context):
        if self.frame is None:
            return
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        gpu.state.blend_set("ALPHA")
        grid = self._grid(context) or 0.05
        lines = []
        centre = self.anchor or (0.0, 0.0)
        for i in range(-GRID_CELLS, GRID_CELLS + 1):   # A small grid around the hover, on the surface.
            for a, b in (((i, -GRID_CELLS), (i, GRID_CELLS)), ((-GRID_CELLS, i), (GRID_CELLS, i))):
                lines += [self.frame.to_world(centre[0] + a[0] * grid, centre[1] + a[1] * grid, 1e-4),
                          self.frame.to_world(centre[0] + b[0] * grid, centre[1] + b[1] * grid, 1e-4)]
        shader.uniform_float("lineWidth", 1.0)
        shader.uniform_float("color", ui_tokens.TEAL_SOFT)
        batch_for_shader(shader, "LINES", {"pos": lines}).draw(shader)
        outline = self.outline
        if self.state == "DRAW":
            try:
                outline = self._drawn_outline() if self.shape != "POLYGON" else self.points + [self.current]
            except ValueError:
                outline = self.points
        if outline:
            loop = [self.frame.to_world(x, y, 1e-4) for x, y in outline]
            if len(loop) > 2 and not (self.state == "DRAW" and self.shape == "POLYGON"):
                loop.append(loop[0])   # Closed, except a polygon still being clicked.
            shader.uniform_float("lineWidth", 2.0)
            shader.uniform_float("color", ui_tokens.TEAL)
            batch_for_shader(shader, "LINE_STRIP", {"pos": loop}).draw(shader)
        gpu.state.blend_set("NONE")


CLASSES = (ANGELO_OT_shape_cut,)


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "shape_cut", TITLE, WHY, glyph="shape_cut", order=46, section=n_("Model"),
        operator=ANGELO_OT_shape_cut.bl_idname, owner="shape_cut_tool"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("shape_cut")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
