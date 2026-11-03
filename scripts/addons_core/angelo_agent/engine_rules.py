# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What each game engine's importer does with a Blender export: the preflight's rules. No Blender dependency.

``evaluate(facts, target, fmt, options, engine_version)`` reads the plain description ``engine_facts.gather`` makes
of a scene and returns findings ``{"code", "severity", "subject", "message", "fix", "facts", "source", "versions",
"fixable"}``. ``facts`` says what the rule looked for and what it found, so a finding is never only pass or fail.
``fixable`` names the typed fix (``engine_fixes``) when one is safe for that subject, else None.

Every rule is derived from an engine's documented import contract or its import code, never from forum threads,
and carries that source (``SOURCES``) and the engine versions it was read from (``VERSIONS``, narrowed per rule
where its source is narrower). A version outside a rule's range skips the rule and says so. Messages are
model-facing English; the person sees the fix card's own translated text (``engine_fix_card``).
doc/angelo/engine_preflight.rst is the contract.
"""

import re

ERROR, WARN, INFO = "error", "warn", "info"
SEVERITIES = (ERROR, WARN, INFO)
TARGETS = ("GODOT", "UNITY", "UNREAL", "WEB")
FORMATS = ("GLB", "FBX")
TARGET_FORMATS = {"GODOT": ("GLB",), "UNITY": ("FBX", "GLB"), "UNREAL": ("GLB", "FBX"), "WEB": ("GLB",)}
DISPLAY = {"GODOT": "Godot", "UNITY": "Unity", "UNREAL": "Unreal Engine", "WEB": "Three.js"}

# The engine versions each target's sources were read from (three.js: its revision number).
VERSIONS = {"GODOT": ("4.0", "4.7"), "UNITY": ("2020.3", "6000.5"), "UNREAL": ("5.0", "5.8"), "WEB": ("152", "185")}

# Most bone influences per vertex the engine renders, from its import or runtime code (SOURCES["influences"]).
ENGINE_INFLUENCES = {"WEB": 4, "GODOT": 8, "UNITY": 4, "UNREAL": 12}

SOURCES = {
    "gltf_units": "glTF 2.0 spec 3.4 (linear distances are metres); io_scene_gltf2 5.2 exports 1 Blender unit as "
                  "1 m and its importer divides by Unit Scale (blender/imp/blender_gltf.py set_convert_functions)",
    "rest_transform": "Godot docs, Retargeting 3D skeletons: Rest Fixer > Apply Node Transforms (4.0+); glTF 2.0 "
                      "spec 3.7.3 (joint matrices are relative to the skeleton's parent nodes)",
    "godot_shapes": "Godot docs, CollisionShape3D: non-uniform scaling of a shape is not supported",
    "fbx_leaf": "io_scene_fbx 5.2 add_leaf_bones (default True); Unity's Unity-BlenderToFBX.py (6000.5) leaves it on",
    "ue_armature": "UE 5.8 Interchange Parsers/Fbx FbxScene.cpp and Ufbx/UfbxScene.cpp: only a root node named "
                   "'armature' (any case) with one child is dropped; any other armature node becomes a bone",
    "ue_root": "UE 5.8 skeletal mesh import: a reference skeleton has exactly one root bone",
    "constraints": "glTF 2.0 and FBX carry transforms, not constraints; io_scene_gltf2 5.2 bakes object motion "
                   "only with export_bake_animation or an action to sample; io_scene_fbx 5.2 samples every object",
    "unity_takes": "Unity-BlenderToFBX.py (6000.5): bake_anim_use_nla_strips and bake_anim_use_all_actions off, "
                   "so the FBX holds one take",
    "blender_orphans": "Blender manual, Data-blocks (Life Time): a data-block with no users is not saved",
    "godot_loop": "Godot 4.7.2 editor/import/3d/resource_importer_scene.cpp (_teststr, loop_strings): an animation "
                  "name ending in -loop, _loop or -cycle imports with Loop Linear",
    "godot_numbered": "Godot 4.0.4 resource_importer_scene.cpp _teststr strips trailing digits and dots, but Godot "
                      "names Blender's .001 _001; 4.1 strips underscores too",
    "gltf_apply": "io_scene_gltf2 5.2 export_apply (default False) and its viewport evaluation; Godot import "
                  "option blender/nodes/modifiers; Unity-BlenderToFBX.py use_mesh_modifiers",
    "faces": "io_scene_gltf2 5.2 and io_scene_fbx 5.2 export a curve or text object's evaluated mesh, faces only",
    "instancing": "glTF 2.0 spec 3.5 (one mesh, many nodes); io_scene_gltf2 5.2 shares a mesh by data block; "
                  "UE 5.8 Interchange UidsOfInstancingNodes; three.js r185 GLTFLoader EXT_mesh_gpu_instancing",
    "visibility": "io_scene_gltf2 5.2 use_visible (default False: hidden and excluded objects are written); "
                  "io_scene_fbx 5.2 writes hidden objects; Godot's Blender import option blender/nodes/visible",
    "fbx_paths": "io_scene_fbx 5.2 fbx_data_texture_file_elements writes FileName (absolute) and RelativeFilename",
    "gltf_extras": "io_scene_gltf2 5.2 export_extras and io_scene_fbx use_custom_props write custom properties "
                   "(on in Godot's and Unity's Blender profiles)",
    "godot_collision": "Godot 4.7.2 resource_importer_scene.cpp: -col/-colonly/-convcolonly on meshes; an empty "
                       "needs the empty_draw_type meta, which only COLLADA sets (gltf_document.cpp keeps extras)",
    "ue_collision": "UE 5.8 Interchange InterchangeMeshHelper.cpp GetMeshCollisionFromName (UBX_, UCX_, MCDCX_, "
                    "USP_, UCP_) and InterchangeLODDataParser.cpp: the rest of the name must match a render mesh",
    "no_collision_names": "Godot 4.7.2 suffixes and UE 5.8 prefixes are each read by one engine only; Unity Manual "
                          "6000.2, Model import settings, has Generate Colliders and no naming; three.js has no "
                          "physics",
    "influences": "three.js r185 GLTFLoader (JOINTS_0/WEIGHTS_0 only); Godot 4.7.2 gltf_document.cpp (JOINTS_0..1, "
                  "8 bones); Unity Manual 6000.2 Rig tab (Skin Weights: Standard 4 bones); UE 5.8 "
                  "GPUSkinPublicDefs.h MAX_TOTAL_INFLUENCES 12; io_scene_gltf2 export_influence_nb (default 4)",
    "colour_space": "glTF 2.0 spec 3.9 (base colour and emissive sRGB, other maps linear); three.js r185 "
                    "GLTFLoader gives SRGBColorSpace to colour maps only; Unity Manual, Texture import sRGB",
}

# code -> (severity, message, fix, source key, targets, formats or None for any).
RULES = {
    "unit_scale": (WARN, "Unit Scale is {scale}: glTF writes one Blender unit as one metre, so the engine sees the "
                         "scene at {factor} times the size Blender shows",
                   "Model at Unit Scale 1, or scale the scene by {scale} and apply it before exporting",
                   "gltf_units", TARGETS, ("GLB",)),
    "transform_unapplied": (WARN, "Armature {subject} has rotation {rotation} and scale {scale}: the engine gets "
                                  "it as a transformed node above the skeleton, which retargeting and root motion "
                                  "don't expect", "Apply its rotation and scale with the meshes it deforms",
                            "rest_transform", TARGETS, None),
    "collider_scale": (WARN, "Collision mesh {subject} has non-uniform scale {scale}: Godot's physics can't scale "
                             "a shape unevenly", "Apply its scale", "godot_shapes", ("GODOT",), None),
    "leaf_bones": (INFO, "The FBX gains a leaf bone at the end of each of {count} bone chains (\"_end\")",
                   "Export with add_leaf_bones off where you control the exporter", "fbx_leaf",
                   ("UNITY", "UNREAL"), ("FBX",)),
    "armature_extra_root": (WARN, "Armature {subject}: Unreal reads this armature object as an extra root bone",
                            "Rename the armature object to Armature (it needs one root bone at the top of the "
                            "scene)", "ue_armature", ("UNREAL",), ("FBX",)),
    "root_bones": (ERROR, "Armature {subject} has {count} root bones ({bones}): Unreal needs one",
                   "Parent every root bone under one root bone", "ue_root", ("UNREAL",), None),
    "constraints_live": (WARN, "{subject}: constraints ({constraints}) don't reach the engine; it gets their result "
                               "only where an animation is sampled",
                         "Bake them (Object > Animation > Bake Action, Visual Keying, Clear Constraints)",
                         "constraints", TARGETS, None),
    "actions_one_take": (WARN, "{subject} uses {count} actions ({actions}), but Unity's FBX profile writes one take",
                         "Use a .glb for this model, or one file per action", "unity_takes", ("UNITY",), ("FBX",)),
    "action_unsaved": (WARN, "Action {subject} has no user, so saving drops it before the engine sees it",
                       "Stash it in a muted NLA track of {owner}", "blender_orphans", TARGETS, None),
    "loop_name": (INFO, "Action {subject} ends where it starts, but its name has no loop suffix: Godot imports it "
                        "without looping", "Rename it {name}-loop", "godot_loop", ("GODOT",), ("GLB",)),
    "smooth_modifier": (WARN, "{subject}: the {modifier} modifier sets its smooth shading, but {why}, so the "
                              "engine shows it faceted", "Apply the modifier", "gltf_apply", TARGETS, None),
    "curve_no_surface": (WARN, "{kind} {subject} has no faces (no bevel, extrusion or fill), so the engine gets "
                               "nothing to draw", "Give it a bevel or extrusion, or convert it to a mesh", "faces",
                         TARGETS, None),
    "instancing_lost": (INFO, "{count} objects ({objects}) have identical meshes in separate data blocks, so the "
                              "file carries {extra} extra copies", "Share one mesh between them (Link Object Data)",
                        "instancing", TARGETS, None),
    "hidden_exported": (WARN, "{count} hidden or excluded objects ({objects}) are still written to the file",
                        "Delete them, or move them to another file, before the engine imports it", "visibility",
                        ("GODOT", "UNITY", "UNREAL"), None),
    "absolute_paths": (WARN, "The FBX records the full path on this computer of {count} images ({images})",
                       "Pack the images, or expect the engine to relink them by file name", "fbx_paths",
                       ("UNITY", "UNREAL"), ("FBX",)),
    "path_properties": (WARN, "{subject}: custom properties ({properties}) hold paths on this computer, and the "
                              "export writes them", "Remove them, or export without custom properties",
                        "gltf_extras", TARGETS, None),
    "collision_empty": (WARN, "Empty {subject} is named for Godot collision, but Godot reads that from COLLADA "
                              "only: from glTF it imports a plain Node3D", "Replace it with a {shape} mesh of the "
                              "same name", "godot_collision", ("GODOT",), ("GLB",)),
    "collision_unmatched": (WARN, "{subject} has a collision prefix but no mesh is named {body}: Unreal imports it "
                                  "as a visible mesh", "Name it after its render mesh (UCX_<mesh> or UCX_<mesh>_01)",
                            "ue_collision", ("UNREAL",), None),
    "collision_foreign": (WARN, "{subject} is named for {convention} collision, which {engine} doesn't read: it "
                                "imports as a visible mesh", "{remedy}", "no_collision_names", TARGETS, None),
    "influences": (WARN, "{subject}: {count} vertices have more than {cap} bone influences; the engine drops "
                         "weights up to {dropped}", "Limit each vertex to {cap} influences and normalize",
                   "influences", TARGETS, None),
    "suffix_numbered": (WARN, "{subject}: Godot {version} doesn't read the -{token} suffix before Blender's number, "
                              "so it imports a plain node", "Rename it so the suffix ends the name", "godot_numbered",
                        ("GODOT",), ("GLB",)),
    "colour_space": (WARN, "Image {subject} is {colorspace} but used as {role} data: the engine reads it as "
                           "{wanted}, so it looks different there", "Set its colour space to {wanted}",
                     "colour_space", TARGETS, None),
}

# Rules whose source covers fewer versions than the target's; they run only for a stated engine_version in range.
NARROWER = {"suffix_numbered": {"GODOT": ("4.0", "4.0")}}

# The typed fix (engine_fixes) per code; the facts still say per subject whether it is safe there.
FIXES = {"transform_unapplied": "apply_transform", "collider_scale": "apply_scale",
         "armature_extra_root": "rename_armature", "action_unsaved": "stash_action", "loop_name": "loop_name",
         "smooth_modifier": "apply_modifier", "instancing_lost": "link_data", "collision_empty": "collision_mesh",
         "collision_foreign": "hide_collider", "influences": "limit_influences", "colour_space": "colour_space"}

GODOT_TOKENS = ("colonly", "convcolonly", "col", "convcol", "rigid")
UNREAL_PREFIXES = ("UBX_", "UCX_", "MCDCX_", "USP_", "UCP_")
LOOP_TOKENS = ("loop_mode", "loop", "cycle")


def _strip_godot(name, numbered=True):
    """The name as Godot's _teststr reads it: Godot names Blender's ".001" "_001", and drops trailing digits, spaces
    and (4.1 and later, ``numbered``) underscores; Godot 4.0 dropped dots instead, so the number's "_" stays."""
    return re.sub(r"[\d\s_]+$" if numbered else r"[\d\s.]+$", "", name.replace(".", "_"))


def godot_token(name, tokens=GODOT_TOKENS, numbered=True):
    """The Godot import suffix ``name`` carries ("colonly"), or None (resource_importer_scene.cpp _teststr);
    ``numbered`` False reads it as Godot 4.0 did."""
    lower = _strip_godot(name, numbered).lower()
    for token in sorted(tokens, key=len, reverse=True):   # "convcolonly" before "colonly" before "col".
        if ("$" + token) in lower or lower.endswith("-" + token) or lower.endswith("_" + token):
            return token
    return None


def unreal_prefix(name):
    """The collision prefix ``name`` starts with, in any case (FString::StartsWith ignores case by default)."""
    folded = name.casefold()
    return next((prefix for prefix in UNREAL_PREFIXES if folded.startswith(prefix.casefold())), None)


def unreal_body_matches(name, meshes):
    """Whether a UCX_-style ``name`` names a render mesh: the rest of the name, or it without its last _suffix,
    equals a mesh name in any case (FString ==), as InterchangeLODDataParser.cpp matches them."""
    rest = name.split("_", 1)[1] if "_" in name else ""
    candidates = {rest.casefold()}
    if "_" in rest:
        candidates.add(rest.rsplit("_", 1)[0].casefold())
    return bool(candidates & {mesh.casefold() for mesh in meshes})


def _version_tuple(text):
    parts = re.findall(r"\d+", str(text or ""))
    return tuple(int(part) for part in parts[:3]) if parts else None


def in_range(target, engine_version, versions=None):
    """True when ``engine_version`` (None: any) falls inside the target's source range (or ``versions``)."""
    version = _version_tuple(engine_version)
    if version is None:
        return True
    low, high = (_version_tuple(value) for value in (versions or VERSIONS[target]))
    return low <= version[:len(low)] and version[:len(high)] <= high


def _names(items, limit=4):
    items = sorted(set(items))
    shown = ", ".join(items[:limit])
    return shown + (" (+%d)" % (len(items) - limit) if len(items) > limit else "")


def _vector(values):
    return "(" + ", ".join("%.3g" % value for value in values) + ")"


class _Findings:
    """The findings of one evaluation, and which rules apply to its target and format."""

    def __init__(self, target, fmt, engine_version=None):
        self.target, self.fmt, self.found, self.engine_version = target, fmt, [], engine_version
        version = _version_tuple(engine_version)
        self.numbered = not (target == "GODOT" and version is not None and version[:2] < (4, 1))

    def applies(self, code):
        _, _, _, _, targets, formats = RULES[code]
        if self.target not in targets or (formats is not None and self.fmt not in formats):
            return False
        narrower = NARROWER.get(code, {}).get(self.target)
        return narrower is None or (self.engine_version is not None
                                    and in_range(self.target, self.engine_version, narrower))

    def add(self, code, subject, looked_for, found, fixable=True, severity=None, **params):
        default, message, fix, source, _, _ = RULES[code]
        params = dict(params, subject=subject)
        low, high = NARROWER.get(code, {}).get(self.target) or VERSIONS[self.target]
        self.found.append({
            "code": code, "severity": severity or default, "subject": subject,
            "message": message.format(**params), "fix": fix.format(**params),
            "facts": {"looked_for": looked_for, "found": found},
            "source": SOURCES[source], "versions": "%s %s to %s" % (DISPLAY[self.target], low, high),
            "fixable": FIXES.get(code) if fixable else None})


def _scene_rules(rules, facts):
    scale = facts["scene"]["unit_scale"]
    if rules.applies("unit_scale") and abs(scale - 1.0) > 1e-6:
        rules.add("unit_scale", "", "Unit Scale 1", "Unit Scale %g" % scale, fixable=False, scale="%g" % scale,
                  factor="%g" % (1.0 / scale))


def _armature_rules(rules, facts, exported):
    for arm in facts["armatures"]:
        name = arm["name"]
        if name not in exported:
            continue
        obj = facts["by_name"][name]
        rotation = obj.get("world_rotation_deg", obj["rotation_deg"])
        scale = obj.get("world_scale", obj["scale"])
        moved = any(abs(value) > 1e-3 for value in rotation) or any(abs(value - 1.0) > 1e-4 for value in scale)
        if moved and rules.applies("transform_unapplied"):
            parent = "" if arm["at_root"] else " (with its parent %s)" % obj["parent"]
            rules.add("transform_unapplied", name, "rotation (0, 0, 0) and scale (1, 1, 1) on the armature in the "
                      "world", "rotation %s degrees, scale %s%s" % (_vector(rotation), _vector(scale), parent),
                      fixable=arm["transform_fix_safe"] and arm["at_root"], rotation=_vector(rotation),
                      scale=_vector(scale) + parent)
        if rules.applies("armature_extra_root"):
            dropped = name.casefold() == "armature" and arm["at_root"] and len(arm["roots"]) == 1
            if not dropped:
                rules.add("armature_extra_root", name,
                          "an armature object named 'armature' at the top of the scene with one root bone",
                          "named '%s', %s, %d root bones" % (name, "at the top" if arm["at_root"] else "parented",
                                                            len(arm["roots"])), fixable=arm["rename_safe"])
        if rules.applies("root_bones") and len(arm["roots"]) > 1:
            rules.add("root_bones", name, "one root bone", "%d root bones" % len(arm["roots"]), fixable=False,
                      count=len(arm["roots"]), bones=_names(arm["roots"]))
    leaves = sum(arm["leaves"] for arm in facts["armatures"] if arm["name"] in exported)
    if leaves and rules.applies("leaf_bones"):
        rules.add("leaf_bones", "", "add_leaf_bones off", "add_leaf_bones on, %d chains" % leaves, fixable=False,
                  count=leaves)


def _animation_rules(rules, facts, exported, options):
    sampled_always = rules.fmt == "FBX" or bool(options.get("export_bake_animation"))
    # A constraint costs motion only where something moves: in a scene without animation the file holds its result.
    moving = any(obj["animated"] for obj in facts["objects"] if obj["name"] in exported)
    for obj in facts["objects"]:
        if obj["name"] not in exported:
            continue
        constraints = list(obj["constraints"]) + list(obj["bone_constraints"])
        if constraints and rules.applies("constraints_live"):
            sampled = obj["animated"] or sampled_always
            rules.add("constraints_live", obj["name"], "no live constraints, or an animation that samples them",
                      "%d constraints, %s%s" % (len(constraints), "sampled" if sampled else "not sampled",
                                                "" if moving else ", nothing animated"),
                      fixable=False, severity=WARN if moving and not sampled else INFO,
                      constraints=_names(constraints))
        if rules.applies("actions_one_take") and len(obj["actions"]) > 1:
            rules.add("actions_one_take", obj["name"], "one action per FBX file", "%d actions" % len(obj["actions"]),
                      fixable=False, count=len(obj["actions"]), actions=_names(obj["actions"]))
    for action in facts["actions"]:
        owners = [owner for owner in action["owners"] if owner in exported]
        if not owners:
            continue
        if action["users"] == 0 and not action["fake_user"] and rules.applies("action_unsaved"):
            rules.add("action_unsaved", action["name"], "a user (active, an NLA strip or a fake user)", "no user",
                      fixable=action.get("editable", True), owner=owners[0])
        if action["cyclic"] and godot_token(action["name"], LOOP_TOKENS, rules.numbered) is None and rules.applies(
                "loop_name"):
            rules.add("loop_name", action["name"], "a loop suffix (-loop, _loop, -cycle)",
                      "first and last keys equal, no suffix", fixable=action.get("editable", True), name=action["name"])


def _mesh_rules(rules, facts, exported, options):
    applies_modifiers = options.get("export_apply", options.get("use_mesh_modifiers", True))
    shown = [obj for obj in facts["objects"] if obj["name"] in exported]
    for obj in shown:
        for modifier in obj["modifiers"]:
            if not modifier["smooth"] or not rules.applies("smooth_modifier"):
                continue
            why = ("this export doesn't apply modifiers" if not applies_modifiers else
                   "it is off in the viewport, which the exporter evaluates" if not modifier["viewport"] else None)
            if why:
                rules.add("smooth_modifier", obj["name"], "smooth shading in the mesh, or a modifier the export "
                          "applies", "modifier %s; %s" % (modifier["name"], why), fixable=modifier["apply_safe"],
                          modifier=modifier["name"], why=why)
        if obj["type"] in {"CURVE", "FONT", "SURFACE", "META"} and obj["faces"] == 0 and rules.applies(
                "curve_no_surface"):
            rules.add("curve_no_surface", obj["name"], "faces in the evaluated mesh", "0 faces", fixable=False,
                      kind={"FONT": "Text", "META": "Metaball"}.get(obj["type"], obj["type"].title()))
    groups = {}
    for obj in shown:
        if obj["geometry_key"]:
            groups.setdefault(obj["geometry_key"], []).append(obj)
    for members in groups.values():
        datas = {obj["data"] for obj in members}
        if len(datas) > 1 and rules.applies("instancing_lost"):
            names = sorted(obj["name"] for obj in members)
            rules.add("instancing_lost", names[0], "one shared mesh for identical geometry",
                      "%d objects, %d mesh data blocks" % (len(members), len(datas)),
                      fixable=all(obj["link_safe"] for obj in members), count=len(members),
                      objects=_names(names), extra=len(datas) - 1)


def _file_rules(rules, facts, exported, options):
    if rules.applies("hidden_exported"):
        hidden = sorted(obj["name"] for obj in facts["objects"] if obj["name"] in exported and obj["hidden"])
        if hidden:
            rules.add("hidden_exported", hidden[0], "only objects the viewport shows",
                      "%d hidden or excluded objects written" % len(hidden), fixable=False, count=len(hidden),
                      objects=_names(hidden))
    if rules.applies("absolute_paths"):
        images = sorted(image["name"] for image in facts["images"]
                        if image["used_by_export"] and not image["packed"] and image["file"])
        if images:
            rules.add("absolute_paths", images[0], "packed images", "%d images by path" % len(images),
                      fixable=False, count=len(images), images=_names(images))
    if options.get("export_extras", options.get("use_custom_props", False)) and rules.applies("path_properties"):
        for obj in facts["objects"]:
            if obj["name"] in exported and obj["path_properties"]:
                rules.add("path_properties", obj["name"], "no paths on this computer in exported custom properties",
                          "%d properties" % len(obj["path_properties"]), fixable=False,
                          properties=_names(obj["path_properties"]))


def _collision_rules(rules, facts, exported):
    meshes = [obj["name"] for obj in facts["objects"] if obj["type"] == "MESH" and obj["name"] in exported]
    for obj in facts["objects"]:
        name = obj["name"]
        if name not in exported:
            continue
        token, prefix = godot_token(name, numbered=rules.numbered), unreal_prefix(name)
        if token is None and rules.applies("suffix_numbered"):
            later = godot_token(name)
            if later:
                rules.add("suffix_numbered", name, "the -%s suffix at the end of the name" % later,
                          "a number after it", fixable=False, version=rules.engine_version, token=later)
        foreign = ("Godot" if token and token != "rigid" and rules.target != "GODOT" else
                   "Unreal" if prefix and obj["type"] == "MESH" and rules.target != "UNREAL" else None)
        if foreign and rules.applies("collision_foreign"):
            web = rules.target == "WEB"
            remedy = ("Hide it in the viewport: the web export writes visible objects only" if web
                      else "Add the collider in %s instead" % DISPLAY[rules.target])
            rules.add("collision_foreign", name, "a collision convention %s reads" % DISPLAY[rules.target],
                      "%s naming" % foreign, fixable=web, convention=foreign, engine=DISPLAY[rules.target],
                      remedy=remedy)
        elif (token and obj["type"] == "EMPTY" and token != "rigid" and not obj.get("omi_physics")
              and rules.applies("collision_empty")):   # An OMI collider reaches Godot 4.4+ with its shape.
            shape = {"CUBE": "box", "SPHERE": "sphere"}.get(obj["empty_display"])
            rules.add("collision_empty", name, "a mesh carrying the -%s suffix" % token, "an empty",
                      fixable=shape is not None and obj.get("replace_safe", False), shape=shape or "box or sphere")
        elif token and obj["type"] == "MESH" and token != "rigid" and rules.applies("collider_scale"):
            if max(obj["scale"]) - min(obj["scale"]) > 1e-4:
                rules.add("collider_scale", name, "uniform scale on a collision mesh", _vector(obj["scale"]),
                          fixable=obj["apply_scale_safe"], scale=_vector(obj["scale"]))
        elif prefix and obj["type"] == "MESH" and rules.applies("collision_unmatched"):
            if not unreal_body_matches(name, [mesh for mesh in meshes if mesh != name]):
                body = name[len(prefix):]
                rules.add("collision_unmatched", name, "a render mesh named %s" % body, "none", fixable=False,
                          body=body)


def influence_cap(target, fmt, options):
    """The influences per vertex that reach the engine: the engine's limit, and glTF's exporter limit."""
    exporter = None
    if fmt == "GLB" and not options.get("export_all_influences"):
        exporter = int(options.get("export_influence_nb", 4))
    return min(value for value in (exporter, ENGINE_INFLUENCES[target]) if value is not None)


def _skin_rules(rules, facts, exported, options):
    cap = influence_cap(rules.target, rules.fmt, options)
    for skin in facts["influences"]:
        over = skin["over"].get(str(cap), 0)
        if skin["object"] in exported and over and rules.applies("influences"):
            dropped = skin["dropped"].get(str(cap), 0.0)
            rules.add("influences", skin["object"], "at most %d influences per vertex" % cap,
                      "%d vertices over, at most %d, largest dropped weight %.3f" % (over, skin["most"], dropped),
                      fixable=skin["limit_safe"], severity=WARN if dropped >= 0.05 else INFO, count=over, cap=cap,
                      dropped="%.3f" % dropped)


def _image_rules(rules, facts):
    if not rules.applies("colour_space"):
        return
    for image in facts["images"]:
        roles = set(image["roles"])
        if not image["used_by_export"] or len(roles) != 1 or image["colorspace"] not in {"sRGB", "Non-Color"}:
            continue   # Used both ways, or a colour space the rule doesn't judge (ACES, Linear, Filmic...).
        role = next(iter(roles))
        wanted = "sRGB" if role == "colour" else "Non-Color"
        if image["colorspace"] != wanted:
            rules.add("colour_space", image["name"], "%s for %s data" % (wanted, role), image["colorspace"],
                      fixable=image.get("editable", True), colorspace=image["colorspace"], role=role, wanted=wanted)


def evaluate(facts, target, fmt, options, engine_version=None):
    """The findings for ``facts`` exported to ``target`` as ``fmt`` with exporter ``options``, errors first; a stated
    ``engine_version`` selects version-specific readings (Godot 4.0's suffixes)."""
    if target not in TARGETS or fmt not in TARGET_FORMATS[target]:
        raise ValueError("%s takes %s from this preflight" % (DISPLAY.get(target, target), " or ".join(
            TARGET_FORMATS.get(target, FORMATS))))
    rules = _Findings(target, fmt, engine_version)
    facts = dict(facts, by_name={obj["name"]: obj for obj in facts["objects"]})
    exported = frozenset(facts["exported"])
    _scene_rules(rules, facts)
    _armature_rules(rules, facts, exported)
    _animation_rules(rules, facts, exported, options)
    _mesh_rules(rules, facts, exported, options)
    _file_rules(rules, facts, exported, options)
    _collision_rules(rules, facts, exported)
    _skin_rules(rules, facts, exported, options)
    _image_rules(rules, facts)
    order = {severity: index for index, severity in enumerate(SEVERITIES)}
    return sorted(rules.found, key=lambda item: (order[item["severity"]], item["code"], item["subject"]))
