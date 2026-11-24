# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The feature guide's scene, modelling, UV and rigging entries (``feature_guide.GUIDES`` holds them in this order, in
its own place): kept apart so each module stays within the size rule. Model-visible English. No Blender."""

from .feature_guide_types import H, U, Guide  # noqa: F401  (H, U: the examples' placeholders)

SCENE_GUIDES = (
    Guide("retopology", "Retopology (clean quad topology)",
          "Makes clean, evenly flowing quad topology for a scanned, sculpted or AI-generated mesh as a new object, "
          "following its sharp and marked edges; works on broken meshes (holes, loose parts, triangle soups), keeps "
          "vertex groups, and can be exactly symmetric.",
          "Chat bar + menu > Model > Retopology; Properties > Mesh > Remesh > Retopology; F3 > Retopology.",
          "angelo_job_start kind RETOPOLOGY with the mesh's handle as target, faces (default 5,000) and symmetry when "
          "the mesh is symmetric (deferred job tools: its fields come with the lookup). The result is a new object; "
          "the receipt has its faces, quad ratio, poles and deviation.",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "RETOPOLOGY", "target": H, "faces": 8000}},),
          menu=("retopology",), doc="retopology", heading="Retopology", requires=("angelo.retopology",),
          suggest="A scanned, sculpted or generated mesh needs topology for animation, UVs or a game engine."),
    Guide("bake_maps", "Bake every map at once",
          "Bakes all of a material's maps (colour, roughness, normal...) in one job, packed separately or for glTF, "
          "Unreal or Unity.",
          "Chat bar + menu > Create > Render > Bake maps…; F3.",
          "angelo_job_start kind BAKE_MAPS with the object's handle as target and an output folder (deferred job "
          "tools: its fields come with the lookup).",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "BAKE_MAPS", "target": H,
                                                               "output": "textures/crate"}},),
          menu=("bake_maps",), doc="long_jobs", heading="Bake every map at once", requires=("angelo.bake_maps",),
          suggest="The person exports to a game engine or the web and the materials use procedural nodes."),
    Guide("render_region", "Region render over the last render",
          "Re-renders only a chosen region over the last finished render, to check a change quickly.",
          "Chat bar + menu > Create > Render > Render region; Render > Render Region Over Last Render; the region "
          "pills in the render window.",
          "angelo_job_start kind RENDER_REGION with region.",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "RENDER_REGION",
                                                               "region": [0.25, 0.25, 0.75, 0.75]}},),
          menu=("render_region",), doc="render_region", heading="Render a region over the last render", requires=("angelo.render_region",),
          suggest="A small change needs checking in a slow final render."),
    Guide("select_through", "Select Through",
          "Lets box, lasso and circle selection reach hidden elements in solid display, as X-ray does, without "
          "changing the display (mesh Edit Mode, paint masks, Particle Edit).",
          "The 3D view header beside X-ray; Director's top bar in Edit Mode; the shading pie (Z); chat bar + menu > "
          "Inspect > Select Through.",
          "A setting of the person's view: tell them where it is; the agent's own selections never need it.",
          menu=("select_through",), doc="select_through", heading="Select Through"),
    Guide("tension_map", "Tension maps",
          "Shows where a deforming mesh stretches or squashes, as an overlay and as data a material can use.",
          "Chat bar + menu > Inspect > Tension map; Add Modifier > Edit > Tension.",
          "angelo_scene_program modifier_add with type TENSION on the object's handle; the overlay is the person's "
          "view setting.",
          examples=({"operation": {"kind": "modifier_add", "target": H, "parameters": {"type": "TENSION"}}},),
          menu=("tension_map",), doc="tension_map", heading="Tension maps", requires=("angelo.tension_map",),
          suggest="Rigging or cloth work where stretching matters."),
    Guide("scatter", "Scatter with biomes",
          "Places assets on a surface by rules (slope, height, a painted mask, distance from objects, noise) in "
          "layers that keep apart by their footprints, as a new object running plain Geometry Nodes; six biome "
          "presets; camera and distance culling and viewport points. The surface and assets are only read.",
          "Chat bar + menu > Create > Scatter or F3 > Scatter: a card on the active surface, for a biome (each "
          "layer's objects from the selection or models found and added from the Library) or the selected objects.",
          "angelo_execute_python running bpy.ops.angelo.scatter with the surface active: layers as JSON (name, "
          "assets [{object_name, weight}], density per m2, spacing, rules slope/height/mask/distance/noise, "
          "transform, seed), or biome (a preset) with biome_assets {layer: [object names]}; seed, density_scale, "
          "cull_viewport/cull_render/cull_distance (camera: cull_camera or the scene's), proxy. Import Library "
          "items first (angelo_library_import); it reports instances per layer.",
          menu=("scatter",), doc="scatter", heading="Scatter", requires=("angelo.scatter",),
          suggest="Dressing terrain or a set with vegetation, rocks or props.",
          examples=({"tool": "angelo_execute_python", "arguments": {
              "description": "Scatter Pine on Terrain's gentle slopes, away from Road",
              "code": "import bpy, json\nground = bpy.data.objects['Terrain']\n"
                      "layers = [{'name': 'Pines', 'assets': [{'object_name': 'Pine'}], 'density': 0.2, "
                      "'rules': {'slope': {'min': 0, 'max': 25, 'soft': 5}, "
                      "'distance': {'objects': ['Road'], 'keep_away': 4, 'falloff': 2}}, 'seed': 1}]\n"
                      "with bpy.context.temp_override(active_object=ground, object=ground, selected_objects=[ground]):\n"
                      "    bpy.ops.angelo.scatter(layers=json.dumps(layers))\n"}},)),
    Guide("camera_look", "Camera look (lens and film)",
          "A lens and film look on the camera: distortion and chromatic aberration, a natural cos^4 vignette from "
          "the real focal length, bloom, streaks and ghosts, halation, a film grade with white balance, grain sized "
          "to the frame, bokeh shape and optional focus breathing; seven presets. Every engine, and the viewport's "
          "camera view through the viewport compositor. CLEAN takes it off and restores what it replaced.",
          "Chat bar + menu > Render tools > Camera Look or F3 > Camera Look: a card with the look, focus breathing, "
          "the camera view's compositor, Match exposure (shutter and ISO in stops) and Take Off.",
          "angelo_execute_python running bpy.ops.angelo.camera_look: preset CLEAN/VINTAGE/ANAMORPHIC/DOCUMENTARY/"
          "WARM_PORTRAIT/BLEACH_BYPASS/NOIR, stages as JSON over the preset's (lens, vignette, glare, halation, "
          "film, grain), camera_values (aperture_blades/rotation/ratio), breathing (+ breathing_amount), camera, "
          "exposure as JSON ({reference: {f_stop, shutter seconds, iso}, setting: {shutter, iso, f_stop?}}: the "
          "film's exposure in stops from the settings the light is right for to the camera's).",
          menu=("camera_look",), doc="camera_look", heading="Camera Look", requires=("angelo.camera_look",),
          suggest="Before a final render or a Director shot that should feel filmed rather than rendered.",
          examples=({"tool": "angelo_execute_python", "arguments": {
              "description": "A vintage look on the scene camera with stronger grain",
              "code": "import bpy, json\n"
                      "bpy.ops.angelo.camera_look(preset='VINTAGE', stages=json.dumps({'grain': {'amount': 0.1}}))\n"}},)),
    Guide("splat_mesh", "Splats to meshes",
          "Turns imported Gaussian splats into a closed collision mesh within a triangle budget (with convex hulls) "
          "or a mesh coloured from the splats, leaving floaters out, and writes splats back to PLY.",
          "Chat bar + menu > Splats to mesh; F3 > Splats to Mesh or Export Splats (.ply).",
          "No typed operation yet: tell the person where it is and which kind to choose (Collision for physics and "
          "game engines, Visual for a coloured mesh).",
          menu=("splat_mesh",), doc="features", heading="Splats to meshes", requires=("angelo.splat_to_mesh",),
          suggest="When the scene holds an imported splat capture and the person needs geometry or collision from it."),
    Guide("collision_hulls", "Collision hulls for game engines",
          "Fits convex collision hulls to a mesh as Game Physics colliders, several for a concave mesh so hollows and "
          "openings stay open; each engine's export writes them in its own form (Unreal UCX_, Unity MeshCollider, "
          "Godot OMI or suffixes).",
          "Chat bar + menu > Collision hulls; F3 > Fit Collision Hulls.",
          "No typed operation yet: tell the person where it is; each hull's Game Physics settings are the "
          "properties a program can read on it.",
          menu=("collision_hulls",), doc="game_physics", heading="Collision hulls for game engines",
          requires=("angelo.fit_collision",),
          suggest="Before sending a concave prop or a level piece to a game engine, or when its collision closes a "
                  "doorway or fills a hollow."),
    Guide("uv_checker", "UV checker",
          "Shows a checker pattern on models in the viewport to judge UV stretching and texel density.",
          "Chat bar + menu > Inspect > UV checker; Director's top bar; View > Toggle UV Checker.",
          "The person's view setting: tell them where it is; angelo_view's UV look shows the agent a layout.",
          menu=("uv_checker",), doc="uv_checker", heading="UV checker", requires=("angelo.uv_checker",),
          suggest="After unwrapping, or before texturing or baking."),
    Guide("shape_cut", "Shape Cut (draw-to-cut hard surface)",
          "Draw a box, circle, polygon or freehand shape on a surface (grid-snapped, aligned to the face) to cut, "
          "add, intersect, slice, inset or panel-line it. Cuts stay live and editable (each is a wire cutter object "
          "driving a Boolean) until applied; clean shading adds sharp edges by angle, an optional Bevel and "
          "Weighted Normal.",
          "Chat bar + menu > Model > Shape Cut; F3 > Shape Cut.",
          "angelo_execute_python running bpy.ops.angelo.shape_cut on the active mesh in Object Mode: a face by "
          "index or towards a direction, BOX size or CIRCLE radius in metres, offset from the face's centre, "
          "operation, depth or through, columns/rows/spacing for grids such as vents. The cut stays live.",
          menu=("shape_cut",), doc="shape_cut", heading="Shape Cut", requires=("angelo.shape_cut",),
          suggest="Hard-surface detail on a panel or prop: vents, recesses, panel lines, holes, slices.",
          examples=({"tool": "angelo_execute_python", "arguments": {
              "description": "Cut five vent slots 5 mm deep into Panel's top face",
              "code": "import bpy\nobj = bpy.data.objects['Panel']\n"
                      "with bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj]):\n"
                      "    bpy.ops.angelo.shape_cut(towards=(0.0, 0.0, 1.0), shape='BOX', size=(0.08, 0.01),"
                      " operation='CUT', depth=0.005, rows=5, spacing=(0.0, 0.02))\n"
                      "result = {'booleans': sum(m.type == 'BOOLEAN' for m in obj.modifiers)}\n"}},)),
    Guide("uv_layout", "UV Layout (unwrap and pack)",
          "Unwraps (optional) and packs meshes for one texture set: a texel density in pixels per metre or an even "
          "fill, padding that survives the mip levels asked for, identical parts stacked, a UDIM tile per material "
          "or object, and optionally the packer's Search (more layouts tried, more time). Returns measured facts.",
          "Chat bar + menu > Model > UV Layout; F3 > UV Layout.",
          "angelo_job_start kind UV_LAYOUT with the mesh's handle as target and layout: texture_size, use_density "
          "+ density (px/m; 10 px/cm = 1000), mips, unwrap KEEP/SEAMS/SMART, stack, mirrored, group_by "
          "NONE/MATERIAL/OBJECT, search, packer QUALITY (default; it runs only as this job). For speed, only when "
          "the person asks: angelo_execute_python running bpy.ops.angelo.uv_layout with packer='FAST' on the "
          "selected meshes. Both report tiles, coverage and density. Search is this layout's: uv_pack keeps "
          "Blender's own fields.",
          menu=("uv_layout",), doc="uv_layout", heading="UV Layout", requires=("angelo.uv_layout",),
          suggest="Before baking or exporting a game asset, after an unwrap, or when a texture set wastes space.",
          examples=({"tool": "angelo_job_start", "arguments": {"kind": "UV_LAYOUT", "target": H, "layout": {
              "texture_size": "2048", "use_density": True, "density": 1024.0, "stack": True}}},
                    {"tool": "angelo_execute_python", "arguments": {
                        "description": "Lay out Crate's UVs quickly (the person asked for speed)",
                        "code": "import bpy\nobj = bpy.data.objects['Crate']\n"
                                "with bpy.context.temp_override(active_object=obj, object=obj,"
                                " selected_objects=[obj]):\n"
                                "    bpy.ops.angelo.uv_layout(texture_size='2048', packer='FAST')\n"
                                "result = {'uv_maps': [m.name for m in obj.data.uv_layers]}\n"}})),
    Guide("engine_skeleton", "Rig check for a game engine (Unreal, Unity, Godot)",
          "Checks whether an armature fits a game engine's humanoid skeleton: Unreal's UE5 Mannequin names and "
          "whether its IK Retargeter would auto-retarget it, Unity Humanoid, Godot's SkeletonProfileHumanoid. Each "
          "bone's role is found by layout; each finding has its fix and engine source. Read-only.",
          "Chat bar + menu > Inspect > Check a rig for a game engine; F3 > Check Rig for Game Engine.",
          "angelo_skeleton_check {target: UNREAL | UNITY | GODOT, armature, format} (deferred, read-only).",
          examples=({"tool": "angelo_skeleton_check", "arguments": {"target": "UNREAL"}},),
          menu=("engine_skeleton",), doc="engine_skeleton", heading="Rig check for a game engine",
          suggest="Before exporting a rigged character to Unreal, Unity or Godot, or when retargeting fails there."),
    Guide("rigging", "Retarget, game rigs, root motion, clips and IK",
          "Works on any humanoid armature by the role each bone plays: retargets an animation between unlike rigs; "
          "makes a deform-only, engine-named game rig (Unreal, Unity, Godot) with every action baked and one root; "
          "moves the hips' travel onto the root or keeps a clip in place; exports one clip per action; adds hand "
          "and foot IK controls with an IK/FK switch; poses the whole body from hand, foot, head or hip targets "
          "within a human range of motion, balanced over planted feet.",
          "Chat bar + menu > Rig > Retarget animation, Make game rig, Root motion, Export clips, IK controls, "
          "Switch IK/FK; F3 > Pose Body.",
          "In a script: bpy.ops.angelo.retarget_animation(source=, target=, root_motion=KEEP|IN_PLACE|ROOT, "
          "limbs=FK|IK), make_game_rig(source=, target_engine=UNREAL|UNITY|GODOT), extract_root_motion(armature=, "
          "mode=ROOT|IN_PLACE, yaw=), add_ik_controls(armature=), set_ik_fk(armature=, mode=IK|FK, limb=ALL|ARMS|"
          "LEGS), pose_body(armature=, targets={'RIGHT_HAND'}, right_hand=(x, y, z)). Exporting clips writes files: "
          "the person picks the file in its browser.",
          examples=({"tool": "angelo_execute_python", "arguments": {
              "description": "Make an Unreal game rig of the control rig, its actions baked",
              "code": "import bpy\nbpy.ops.angelo.make_game_rig(source='rig', target_engine='UNREAL')\n"
                      "result = sorted(o.name for o in bpy.data.objects if o.get('angelo_game_rig_of') == 'rig')"}},),
          menu=("rig_retarget", "rig_game_rig", "rig_root_motion", "rig_clips", "rig_ik_controls", "rig_ik_fk"),
          doc="rigging", heading="Rigging: retarget, game rigs and IK", requires=("angelo.retarget_animation",),
          suggest="Before exporting a character to a game engine, or when an animation must move to another rig."),
)
