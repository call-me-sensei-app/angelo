# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One clip per action, the way each engine imports clips (doc/angelo/rigging.rst, "Clips").

``export_action_clips(armature, filepath, fmt, replace)`` writes the armature and the meshes skinned to it with one
animation per clip (``clip_set``: the active action, and each unmuted single-strip track, with its own slot, frames,
trim and scale; muted ones are not clips; names unique). Both formats play the clips on a stage of copies
(``clip_stage``), so the person's actions, NLA, slots and selection are never changed:

* ``GLB`` (Godot, Unreal, the web): one file whose animations are exactly the clips, one per track of the stage
  (Blender's exporter writes one animation per NLA track; shape-key tracks of one name merge into it). Shared glTF
  options: ``engine_link_assets.GLTF_DURABLE``.
* ``FBX`` (Unity): ``<model>.fbx`` with the meshes and skeleton and no animation, posed as the first clip's first
  frame, and one ``<model>@<clip>.fbx`` per clip with that clip as its only take, baked over the clip's own frames
  (the scene's range is the clip's while it plays): the armature only, and the meshes whose shape keys play in that
  clip (a track of it, or drivers). Unity's FBX options (``unity_link_assets.UNITY_FBX``).

Data linked from another file or a library override is refused by name (the stage cannot rename or copy it), and so
is a hidden or excluded object (it would be missing from the file). **Where it writes**
(doc/angelo/script_confinement.rst, "Angelo's own I/O"): the file the person picked (``owned_paths.require_writable``:
C counts the save browser's pick for that run only) and, for FBX, the clip files beside it, each made by C
exclusively (``owned_paths.create_beside``). An existing clip file that Angelo did not make this session is the
person's: it is replaced only when the person ticked Replace in the same browser (``replace``), and only for that
call (``owned_paths.vouched_during``: C's call scope, refused while a script runs); otherwise the export is refused
naming it. A script's export has no pick: C refuses it the picked file, and lets it replace clip files only where
Angelo made them. Clip files this export made are removed again if it fails. The glTF export names the active scene
(``use_active_scene``), so no other scene's selection reaches the file.
"""

import bpy

from . import clip_paths, clip_set, clip_stage, owned_paths
from .engine_link_assets import GLTF_DURABLE
from .rig_edit import RigError
from .unity_link_assets import UNITY_FBX


def _skinned(armature):
    return [o for o in bpy.context.scene.objects
            if o.type == 'MESH' and (o.parent is armature or any(
                m.type == 'ARMATURE' and m.object is armature for m in o.modifiers))]


class _Selection:
    def __init__(self, objects):
        self.objects = objects

    def __enter__(self):
        self.before = [o for o in bpy.context.scene.objects if o.select_get()]
        self.active = bpy.context.view_layer.objects.active
        for obj in bpy.context.scene.objects:
            obj.select_set(obj in self.objects)

    def __exit__(self, *exc):
        for obj in bpy.context.scene.objects:
            obj.select_set(obj in self.before)
        bpy.context.view_layer.objects.active = self.active


def write_gltf(filepath):
    owned_paths.require_writable(filepath)
    bpy.ops.export_scene.gltf(filepath=filepath, use_selection=True, use_active_scene=True, export_animations=True,
                              export_animation_mode='NLA_TRACKS', export_force_sampling=True, **GLTF_DURABLE)


def write_fbx(filepath, objects, bake):
    owned_paths.require_writable(filepath)
    options = dict(UNITY_FBX, use_selection=True)
    options["object_types"] = set(objects)   # The profile is JSON-shaped; the operator takes a set.
    bpy.ops.export_scene.fbx(filepath=filepath, bake_anim=bake, **options)


def _glb(armature, meshes, clips, filepath):
    owned_paths.require_writable(filepath)
    with clip_stage.stage(armature, meshes, clips) as staged, _Selection([staged.armature] + staged.meshes):
        write_gltf(filepath)
    return [filepath]


def _fbx(armature, meshes, clips, filepath, replace):
    model = clip_paths.model_file(filepath)
    paths = clip_paths.clip_files(model, [clip.name for clip in clips])
    with owned_paths.vouched_during([p for p in replace if p in paths.values()], folder=False):
        made = clip_paths.claim(model, list(paths.values()), confirmed=replace)
        files = [model]
        try:
            with clip_stage.stage(armature, meshes, clips) as staged:
                with clip_stage.solo(staged, clips[0]), _Selection([staged.armature] + staged.meshes):
                    write_fbx(model, UNITY_FBX["object_types"], bake=False)   # The first clip's first pose.
                for clip in clips:
                    with clip_stage.solo(staged, clip) as keyed, _Selection([staged.armature] + keyed):
                        path = paths[clip.name]
                        write_fbx(path, ['ARMATURE', 'MESH'] if keyed else ['ARMATURE'], bake=True)
                        files.append(path)
        except BaseException:
            clip_paths.release(made)   # Clip files this export made and could not fill.
            raise
    return files


def export_action_clips(armature, filepath, fmt='GLB', replace=()):
    """Returns {"clips": [clip names], "files": [paths written]}. ``replace``: existing clip files of the person's
    they ticked to replace (FBX; ``clip_paths.claim``)."""
    meshes = _skinned(armature)
    clip_stage.editable_or_refuse([armature] + meshes)
    clips = clip_set.clips(armature)
    if not clips:
        raise RigError("no_action")
    clip_stage.visible_or_refuse([armature] + meshes)
    if fmt == 'FBX':
        files = _fbx(armature, meshes, clips, filepath, replace)
    else:
        files = _glb(armature, meshes, clips, filepath)
    return {"clips": [clip.name for clip in clips], "files": files}
