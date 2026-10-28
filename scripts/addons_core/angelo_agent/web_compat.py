# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a scene loses on its way to Three.js, each with its fix. No Blender dependency.

``findings(facts)`` reads the plain description ``web_facts.gather`` makes of a scene and returns findings:
``{"code", "severity", "subject", "message", "fix", "params"}``. ``message`` and ``fix`` are English templates
with ``{placeholders}`` (marked with ``n_`` so the preview page shows them translated, ``render``); the agent reads
them in English (``english``). A finding says what differs. Nothing here claims the web result matches Blender:
where a shader graph is only partly representable the finding says so instead of guessing.

The node classes follow what Blender's glTF exporter (io_scene_gltf2, 5.2) reads from a material: the Principled
BSDF and the textures, maps and separations feeding it, Emission and Background (unlit). Nodes it reads only in
simple arrangements are ``PARTIAL``; everything else is dropped from the exported material.
"""

from .ui_text import n_

ERROR, WARN, INFO = "error", "warn", "info"
SEVERITIES = (ERROR, WARN, INFO)

CARRIED_NODES = frozenset({
    "ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial", "ShaderNodeTexImage", "ShaderNodeNormalMap",
    "ShaderNodeUVMap", "ShaderNodeMapping", "ShaderNodeTexCoord", "ShaderNodeSeparateColor", "ShaderNodeSeparateRGB",
    "ShaderNodeSeparateXYZ", "ShaderNodeEmission", "ShaderNodeBackground", "ShaderNodeVertexColor",
    "ShaderNodeAttribute", "NodeFrame", "NodeReroute", "NodeGroupInput", "NodeGroupOutput"})
# Read only when they mix a constant, an alpha or a vertex colour into a carried input.
PARTIAL_NODES = frozenset({
    "ShaderNodeMixShader", "ShaderNodeMix", "ShaderNodeMixRGB", "ShaderNodeMath", "ShaderNodeInvert",
    "ShaderNodeValue", "ShaderNodeRGB", "ShaderNodeBsdfDiffuse", "ShaderNodeBsdfTransparent", "ShaderNodeClamp",
    "ShaderNodeMapRange", "ShaderNodeCombineColor", "ShaderNodeCombineRGB", "ShaderNodeCombineXYZ"})
# Node groups the exporter reads by name (its own occlusion and settings groups).
EXPORTER_GROUPS = frozenset({"glTF Material Output", "glTF Settings"})
CARRIED_LIGHTS = frozenset({"POINT", "SPOT", "SUN"})
DROPPED_OBJECTS = frozenset({"VOLUME", "GREASEPENCIL", "GPENCIL", "LIGHT_PROBE", "SPEAKER", "LATTICE"})
# Modifiers whose result moves over time: the export keeps the frame it was made on.
TIME_MODIFIERS = frozenset({"WAVE", "OCEAN", "CLOTH", "SOFT_BODY", "FLUID", "DYNAMIC_PAINT", "EXPLODE",
                            "MESH_CACHE", "MESH_SEQUENCE_CACHE", "PARTICLE_INSTANCE"})
LARGE_TEXTURE = 4096   # Pixels on a side many phones' WebGL cannot sample (MAX_TEXTURE_SIZE 4096).

TEMPLATES = {
    "material_nodes": (WARN, n_("Material {subject}: {nodes} won't carry over; the web shows the Principled BSDF's "
                                "own values where they were connected"),
                       n_("Bake the material to image textures (an Angelo bake job), then export again")),
    "material_partial": (INFO, n_("Material {subject}: {nodes} carry over only in simple uses (a constant, an alpha "
                                  "or a vertex colour mix)"),
                         n_("Check the preview; bake the material if it looks different")),
    "material_displacement": (WARN, n_("Material {subject}: displacement is dropped"),
                              n_("Bake it to a normal map, or apply it as a Displace modifier")),
    "material_volume": (WARN, n_("Material {subject}: volume shading is dropped"),
                        n_("Use a transparent surface material, or render the volume to a texture or video")),
    "material_subsurface": (INFO, n_("Material {subject}: subsurface scattering is dropped"),
                            n_("Bake its look into the base colour")),
    "material_empty": (WARN, n_("Material {subject}: its output has no supported surface shader"),
                       n_("Connect a Principled BSDF to the Material Output")),
    "light_type": (WARN, n_("Light {subject}: {kind} lights don't carry over"),
                   n_("Use a point, spot or sun light, or an emissive surface")),
    "light_soft": (INFO, n_("Light {subject}: soft shadow radius doesn't carry over; Three.js shadows are sharp "
                            "unless your page softens them"),
                   n_("Set shadow radius and map size in your Three.js page")),
    "world": (INFO, n_("The world ({subject}) isn't exported; the preview lights the scene with a neutral studio "
                       "environment"),
              n_("Load the same HDRI in Three.js (RGBELoader with PMREMGenerator) for matching reflections")),
    "object_type": (WARN, n_("Object {subject}: {kind} objects don't carry over"),
                    n_("Convert it to a mesh (Object > Convert > Mesh) if it should appear on the web")),
    "object_meta": (INFO, n_("Object {subject}: metaballs may not export"),
                    n_("Convert it to a mesh (Object > Convert > Mesh)")),
    "particles": (WARN, n_("Object {subject}: particle system {name} ({kind}) doesn't carry over"),
                  n_("Make the instances real, or convert hair to a mesh, before exporting")),
    "time_modifier": (INFO, n_("Object {subject}: the {kind} modifier is frozen at the current frame"),
                      n_("Bake the motion to shape keys or an armature if the web needs it to move")),
    "texture_udim": (ERROR, n_("Image {subject}: UDIM tiles don't carry over"),
                     n_("Bake the tiles into one texture")),
    "texture_video": (WARN, n_("Image {subject}: movie and sequence textures don't carry over"),
                      n_("Use a VideoTexture in your Three.js page")),
    "texture_missing": (ERROR, n_("Image {subject}: its file is missing, so the web gets no texture"),
                        n_("Relink or pack the image (File > External Data)")),
    "texture_large": (WARN, n_("Image {subject} is {width} × {height}: larger than many phones can show"),
                      n_("Scale it to 2048 pixels or less on its longest side")),
    "motion_blur": (INFO, n_("Motion blur doesn't carry over"),
                    n_("Add it as a post-processing pass in your Three.js page")),
    "depth_of_field": (INFO, n_("Camera {subject}: depth of field doesn't carry over"),
                       n_("Add a bokeh pass in your Three.js page")),
    "compositor": (INFO, n_("Compositor effects (glare, colour grading, lens) don't carry over"),
                   n_("Recreate them with Three.js post-processing (EffectComposer)")),
    "web_shader": (INFO, n_("Material {subject}: the browser shows its web shader ({kind}); Angelo shows its nodes "
                            "as the stand-in"),
                   n_("See it in Live Web Preview")),
    "web_shader_file": (ERROR, n_("Material {subject}: its web shader file can't be used ({problem}), so the web "
                                  "shows its nodes"),
                        n_("Choose a GLSL file (.glsl or .frag) under 256 KB in the project")),
    "web_shader_tsl": (WARN, n_("Material {subject}: TSL web shaders arrive in 1.1, so the web shows its nodes"),
                       n_("Write the shader in GLSL for now")),
    "triangles": (WARN, n_("{count} triangles: heavy for phones and slow to download"),
                  n_("Decimate or remesh the densest objects, or remove hidden detail")),
}
HEAVY_TRIANGLES = 1_000_000


def _finding(code, subject="", **params):
    severity, message, fix = TEMPLATES[code]
    return {"code": code, "severity": severity, "subject": subject, "message": message, "fix": fix,
            "params": dict(params, subject=subject)}


def _names(nodes, limit=4):
    """A short, stable list of node labels ("Noise Texture, Color Ramp and 2 more")."""
    nodes = sorted(set(nodes))
    shown = ", ".join(nodes[:limit])
    return shown + (" (+%d)" % (len(nodes) - limit) if len(nodes) > limit else "")


def _material(material):
    out = []
    name = material["name"]
    shader = material.get("web_shader")
    if shader:
        if shader.get("problem") == "tsl":
            out.append(_finding("web_shader_tsl", name))
        elif shader.get("problem"):
            out.append(_finding("web_shader_file", name, problem=shader["problem"].replace("_", " ")))
        else:   # Its nodes are only the stand-in: what they would lose is not the web's look.
            return [_finding("web_shader", name, kind=shader["kind"].title())]
    nodes = material.get("nodes", ())
    dropped = [node["label"] for node in nodes if node["type"] not in CARRIED_NODES | PARTIAL_NODES
               and not (node["type"] == "ShaderNodeGroup" and node.get("group") in EXPORTER_GROUPS)]
    partial = [node["label"] for node in nodes if node["type"] in PARTIAL_NODES]
    if dropped:
        out.append(_finding("material_nodes", name, nodes=_names(dropped)))
    if partial:
        out.append(_finding("material_partial", name, nodes=_names(partial)))
    if material.get("displacement"):
        out.append(_finding("material_displacement", name))
    if material.get("volume"):
        out.append(_finding("material_volume", name))
    if material.get("subsurface"):
        out.append(_finding("material_subsurface", name))
    if material.get("uses_nodes") and not material.get("surface"):
        out.append(_finding("material_empty", name))
    return out


def _object(obj):
    out = []
    name, kind = obj["name"], obj["type"]
    if kind in DROPPED_OBJECTS:
        out.append(_finding("object_type", name, kind=kind.replace("_", " ").title()))
    elif kind == "META":
        out.append(_finding("object_meta", name))
    for system in obj.get("particles", ()):
        out.append(_finding("particles", name, name=system["name"], kind=system["kind"].title()))
    for modifier in sorted(set(obj.get("modifiers", ())) & TIME_MODIFIERS):
        out.append(_finding("time_modifier", name, kind=modifier.replace("_", " ").title()))
    return out


def findings(facts):
    """Every finding for ``facts`` (``web_facts.gather``), errors first, then by code and subject."""
    out = []
    for material in facts.get("materials", ()):
        out.extend(_material(material))
    for obj in facts.get("objects", ()):
        out.extend(_object(obj))
    for light in facts.get("lights", ()):
        if light["type"] not in CARRIED_LIGHTS:
            out.append(_finding("light_type", light["name"], kind=light["type"].title()))
        elif light.get("soft"):
            out.append(_finding("light_soft", light["name"]))
    world = facts.get("world")
    if world and world.get("lit"):
        out.append(_finding("world", world["name"]))
    for image in facts.get("images", ()):
        if image.get("source") == "TILED":
            out.append(_finding("texture_udim", image["name"]))
        elif image.get("source") in {"MOVIE", "SEQUENCE"}:
            out.append(_finding("texture_video", image["name"]))
        elif image.get("missing"):
            out.append(_finding("texture_missing", image["name"]))
        elif max(image.get("width", 0), image.get("height", 0)) > LARGE_TEXTURE:
            out.append(_finding("texture_large", image["name"], width=image["width"], height=image["height"]))
    effects = facts.get("effects", {})
    if effects.get("motion_blur"):
        out.append(_finding("motion_blur"))
    for camera in effects.get("depth_of_field", ()):
        out.append(_finding("depth_of_field", camera))
    if effects.get("compositor"):
        out.append(_finding("compositor"))
    if facts.get("triangles", 0) > HEAVY_TRIANGLES:
        out.append(_finding("triangles", count=facts["triangles"]))
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    return sorted(out, key=lambda item: (order[item["severity"]], item["code"], item["subject"]))


def english(finding):
    """The finding as the agent reads it: message and fix filled in, in English."""
    return {"code": finding["code"], "severity": finding["severity"], "subject": finding["subject"],
            "message": finding["message"].format(**finding["params"]), "fix": finding["fix"].format(**finding["params"])}


def render(finding, translate):
    """The finding as the preview page shows it: ``translate`` (``ui_text.iface_``) applied to each template."""
    params = finding["params"]
    return {"code": finding["code"], "severity": finding["severity"], "subject": finding["subject"],
            "message": translate(finding["message"]).format(**params), "fix": translate(finding["fix"]).format(**params)}
