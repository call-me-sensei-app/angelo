# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scatter: assets placed on the active mesh by rules, as a new Geometry Nodes system (doc/angelo/scatter.rst).

One operator, ``angelo.scatter``: invoked by a person it opens the Scatter card (``scatter_card``: a biome or the
selected objects, Library models per layer); it also runs from its properties alone (EXEC_DEFAULT: the agent's ``scatter``
operation through the shared typed-operation pattern, ``scatter_ops``): checked ``layers`` as JSON, or a ``biome``
preset whose layers take their objects from ``biome_assets``. The surface and the assets are only read; the system
is one new object and one new node group, so Undo removes it in one step. Reached from the chat bar's + menu and F3.
"""

import json
import time

import bpy

from . import scatter_spec, scatter_system
from .ui_text import iface_, n_

TITLE = n_("Scatter", "Angelo")   # Blender's own "Scatter" is light scattering in several languages.
WHY = n_("Place assets on a surface by slope, height, masks and distance")
GLYPH = "scatter"
MAX_ESTIMATE = 1_000_000   # Instances one system may place at full density: beyond it, lower density or split.


def _object(name, what):
    obj = bpy.data.objects.get(name) if isinstance(name, str) else None
    if obj is None:
        raise scatter_spec.ScatterError("%s: no object is named %s" % (what, name), code="NOT_FOUND")
    return obj


def resolve_assets(layers, surface):
    """Each layer's asset objects, in its assets' order. Library items must be imported first."""
    out = []
    for layer in layers:
        objects = []
        for asset in layer["assets"]:
            if "library_id" in asset or "query" in asset:
                raise scatter_spec.ScatterError(
                    "Layer %s names a Library item; import it first (angelo_library_import), then name its object"
                    % layer["name"], code="VALIDATION")
            obj = _object(asset.get("object") or asset.get("object_name"), "Layer %s" % layer["name"])
            if obj == surface:
                raise scatter_spec.ScatterError("Layer %s scatters the surface on itself; pick other objects"
                                                % layer["name"], code="VALIDATION")
            if max(obj.dimensions[0], obj.dimensions[1]) <= 0.0:
                raise scatter_spec.ScatterError("%s has no width on the ground to space it by" % obj.name,
                                                code="VALIDATION")
            objects.append(obj)
        for name in layer["rules"].get("distance", {}).get("objects", ()):
            _object(name, "Layer %s's distance rule" % layer["name"])   # i18n: part of a refusal the agent reads
        out.append(objects)
    return out


def surface_area(surface):
    """The surface's area in world space (its triangles after the object's transform), in square scene units."""
    import numpy as np

    mesh = surface.data
    mesh.calc_loop_triangles()
    if not mesh.loop_triangles:
        return 0.0
    co = np.zeros(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    matrix = np.array(surface.matrix_world)
    co = co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    tri = np.zeros(len(mesh.loop_triangles) * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri)
    a, b, c = (co[tri.reshape(-1, 3)[:, k]] for k in range(3))
    return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() * 0.5)


def biome_layers(name, assets):
    """A preset's layers with their assets replaced by ``assets`` ({layer name: [object names]}); every layer
    needs objects, so a preset is applied only with the person's or the agent's choices."""
    path = scatter_spec.PRESET_DIR / ("%s.angelo-biome.json" % name)   # i18n: a shipped preset's file name
    if not path.is_file():
        raise scatter_spec.ScatterError("No biome preset is named %s" % name, code="NOT_FOUND")
    biome = scatter_spec.load_biome(path.read_text(encoding="utf-8"))
    missing = [layer["name"] for layer in biome["layers"] if not assets.get(layer["name"])]
    if missing:
        raise scatter_spec.ScatterError("biome_assets names no objects for: %s" % ", ".join(missing),
                                        code="VALIDATION")
    unknown = set(assets) - {layer["name"] for layer in biome["layers"]}
    if unknown:
        raise scatter_spec.ScatterError("The %s preset has no layer named %s" % (name, ", ".join(sorted(unknown))),
                                        code="VALIDATION")
    raw = json.loads(json.dumps(biome["layers"]))
    for layer in raw:
        layer["assets"] = [{"object_name": item} for item in assets[layer["name"]]]
    return scatter_spec.check_layers(raw)


def selection_layers(names, density=1.0, spacing=scatter_spec.DEFAULT_SPACING, max_slope=90.0, align=0.0,
                     scale=tuple(scatter_spec.DEFAULT_TRANSFORM["scale"]), seed=0):
    """One checked layer of the objects ``names`` (a person's selection, the surface left out)."""
    if not names:
        raise scatter_spec.ScatterError("Select the objects to scatter, then the surface last (active)",
                                        code="VALIDATION")
    return scatter_spec.check_layers([{
        "name": "Layer", "assets": [{"object_name": name} for name in names[:scatter_spec.MAX_ASSETS]],
        "density": density, "spacing": spacing, "seed": seed,
        "rules": {"slope": {"min": 0.0, "max": max_slope}} if max_slope < 90.0 else {},
        "transform": {"scale": [min(scale), max(scale)], "align": align}}])


def request_layers(layers="", biome="", biome_assets=""):
    """Checked layers from the operator's JSON ``layers``, or from a ``biome`` preset with ``biome_assets``."""
    if layers and biome:
        raise scatter_spec.ScatterError("Give layers or a biome, not both", code="VALIDATION")
    if not (layers or biome):
        raise scatter_spec.ScatterError("Give layers, or a biome with biome_assets", code="VALIDATION")
    if biome:
        try:
            assets = json.loads(biome_assets or "{}")
        except ValueError:
            assets = None
        if not isinstance(assets, dict):
            raise scatter_spec.ScatterError("biome_assets is {layer name: [object names]}", code="VALIDATION")
        return biome_layers(biome, assets)
    try:
        value = json.loads(layers)
    except ValueError:
        raise scatter_spec.ScatterError("layers is JSON", code="VALIDATION") from None
    return scatter_spec.check_layers(value, handles=lambda item, what: item)


OPTIONS = ("seed", "density_scale", "cull_camera", "cull_viewport", "cull_render", "cull_distance", "proxy",
           "viewport_display", "system_name")


def apply(context, surface, layers, seed=0, density_scale=1.0, cull_camera="", cull_viewport=False,
          cull_render=False, cull_distance=0.0, proxy=False, viewport_display=1.0, system_name="Scatter"):
    """A new scatter system on ``surface`` for checked ``layers``; its receipt. Every refusal comes first."""
    assets = resolve_assets(layers, surface)
    area = surface_area(surface)
    estimate = int(sum(layer["density"] for layer in layers) * area * density_scale)
    if estimate > MAX_ESTIMATE:
        raise scatter_spec.ScatterError(
            "About %d instances at this density on %.0f square metres; lower density or density_scale (at most %d)"
            % (estimate, area, MAX_ESTIMATE), code="TOO_LARGE")
    camera = None
    if cull_viewport or cull_render or cull_distance > 0.0:
        camera = _object(cull_camera, "cull_camera") if cull_camera else context.scene.camera
        if camera is None or camera.type != "CAMERA":
            raise scatter_spec.ScatterError("Culling keeps a camera's view; name a camera or set the scene's",
                                            code="VALIDATION")
    start = time.perf_counter()
    system, radii = scatter_system.create(context, surface, layers, assets, name=system_name or "Scatter")
    scatter_system.set_inputs(system, {"Seed": seed, "Density Scale": density_scale, "Proxy": proxy,
                                       "Viewport Display": viewport_display})
    if camera is not None:
        scatter_system.set_culling(system, context.scene, camera, cull_viewport, cull_render, cull_distance)
    facts = scatter_system.facts(system, context.evaluated_depsgraph_get())
    return {"object": system.name, "surface": surface.name, "instances": facts["instances"],
            "layers": [{"name": layer["name"], "instances": count, "radius": round(radius, 4)}
                       for layer, count, radius in zip(layers, facts["layers"], radii)],
            "estimate": estimate, "build_ms": round((time.perf_counter() - start) * 1e3, 1), "proxy": proxy}


def apply_request(context, surface, properties):
    """``apply`` for ``angelo.scatter``'s JSON properties (``layers``, or ``biome`` with ``biome_assets``) and the
    defaults of its other options: the Scatter card's path, the same as the operator's EXEC."""
    unknown = set(properties) - {"layers", "biome", "biome_assets"}
    if unknown:
        raise ValueError("apply_request takes layers, biome and biome_assets")
    return apply(context, surface, request_layers(**properties))


class ANGELO_OT_scatter(bpy.types.Operator):
    bl_idname = "angelo.scatter"
    bl_label = TITLE
    bl_translation_context = "Angelo"   # Its label in TITLE's context.
    bl_description = WHY
    bl_options = {"REGISTER", "UNDO"}

    layers: bpy.props.StringProperty(name="Layers", default="", options={"SKIP_SAVE", "HIDDEN"},
                                     description="The layers as JSON (scatter_spec)")
    biome: bpy.props.StringProperty(name="Biome", default="", options={"SKIP_SAVE", "HIDDEN"},
                                    description="A biome preset's name; its layers take biome_assets' objects")
    biome_assets: bpy.props.StringProperty(name="Biome Assets", default="", options={"SKIP_SAVE", "HIDDEN"},
                                           description="JSON: the object names for each layer")
    density: bpy.props.FloatProperty(name="Per Square Metre", default=1.0, min=0.001, max=scatter_spec.MAX_DENSITY)
    spacing: bpy.props.FloatProperty(name="Spacing", default=1.0, min=0.0, max=scatter_spec.MAX_SPACING,
                                     description="Multiplies each asset's footprint radius: 1 lets footprints touch")
    max_slope: bpy.props.FloatProperty(name="Steepest Slope", default=90.0, min=0.0, max=180.0)
    align: bpy.props.FloatProperty(name="Follow Surface", default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    scale_min: bpy.props.FloatProperty(name="Smallest", default=0.8, min=0.01, max=100.0)
    scale_max: bpy.props.FloatProperty(name="Largest", default=1.2, min=0.01, max=100.0)
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0)
    density_scale: bpy.props.FloatProperty(name="Density Scale", default=1.0, min=0.0, max=1.0, subtype="FACTOR")
    cull_camera: bpy.props.StringProperty(name="Camera", default="", options={"SKIP_SAVE"},
                                          description="The camera culling keeps the view of (empty: the scene's)")
    cull_viewport: bpy.props.BoolProperty(name="Cull in Viewport", default=False)
    cull_render: bpy.props.BoolProperty(name="Cull in Render", default=False)
    cull_distance: bpy.props.FloatProperty(name="Cull Beyond", default=0.0, min=0.0, max=1e6, unit="LENGTH",
                                           description="Drop instances farther from the camera (0: no limit)")
    proxy: bpy.props.BoolProperty(name="Points in Viewport", default=False,
                                  description="Draw points in the viewport instead of the assets; renders keep them")
    viewport_display: bpy.props.FloatProperty(name="Viewport Share", default=1.0, min=0.0, max=1.0,
                                              subtype="FACTOR")
    system_name: bpy.props.StringProperty(name="Name", default="Scatter", maxlen=63)
    from_selection: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE", "HIDDEN"},
                                           description="The selected objects as one layer, with the values above")

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and context.active_object is not None \
            and context.active_object.type == "MESH"

    def invoke(self, context, event):
        """A person's Scatter is the card (scatter_card), which applies through ``apply_request`` as the agent's EXEC
        path does; this run changes nothing, so it pushes no Undo step."""
        from . import card_host, scatter_card
        if not card_host.is_active("scatter"):
            surface = context.active_object.name if context.active_object else None
            card_host.request(scatter_card.ScatterCard(surface, [obj.name for obj in context.selected_objects
                                                                 if obj.type != 'CAMERA']))
            card_host.pump(lambda: True)
        return {"CANCELLED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "density")
        layout.prop(self, "spacing")
        layout.prop(self, "max_slope")
        layout.prop(self, "align")
        row = layout.row(align=True)
        row.prop(self, "scale_min")
        row.prop(self, "scale_max")
        layout.prop(self, "seed")
        layout.separator()
        layout.prop(self, "cull_viewport")
        layout.prop(self, "cull_render")
        layout.prop(self, "cull_distance")
        layout.prop(self, "proxy")

    def _layers(self, context, surface):
        if self.layers or self.biome or not self.from_selection:
            return request_layers(self.layers, self.biome, self.biome_assets)
        assets = [obj.name for obj in context.selected_objects if obj != surface and obj.type != "CAMERA"]
        return selection_layers(assets, self.density, self.spacing, self.max_slope, self.align,
                                (self.scale_min, self.scale_max), self.seed)

    def run(self, context):
        surface = context.active_object
        options = {name: getattr(self, name) for name in OPTIONS}
        return apply(context, surface, self._layers(context, surface), **options)

    def execute(self, context):
        try:
            receipt = self.run(context)
        except scatter_spec.ScatterError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        empty = [layer["name"] for layer in receipt["layers"] if not layer["instances"]]
        self.report({"WARNING"} if empty else {"INFO"}, iface_(
            "{count} instances in {layers} layer(s) on {surface}{empty}", count=receipt["instances"],
            layers=len(receipt["layers"]), surface=receipt["surface"],
            empty=iface_("; nothing placed for: {names}", names=", ".join(empty)) if empty else ""))
        return {"FINISHED"}


CLASSES = (ANGELO_OT_scatter,)


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        # After Workflows (25), before Start from's Base meshes (40): the last Create tile, under Create's heading.
        "scatter", TITLE, WHY, glyph=GLYPH, order=30, section=chat_destinations.CREATE,
        operator=ANGELO_OT_scatter.bl_idname, owner="scatter_ui"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("scatter")
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
