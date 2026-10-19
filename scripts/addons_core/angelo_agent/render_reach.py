# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a render of one scene reaches beyond its own camera: File Output nodes and Sequencer scene strips.

A render writes files beside its image when a compositor runs an unmuted File
Output node. With its Sequencer on, it also renders other scenes: a scene strip
with Camera input renders that scene through its own render pipeline (its own
compositor, File Output nodes, samples and other render settings), and one with
Sequencer input renders that scene's strips in turn (Blender's
``seq_render_scene_strip_ex`` and ``do_render_strip_seqbase``). Admission uses
this module so a render never writes a file its job did not name:

* a look (``view_render``, one private PNG, no approval) refuses an unmuted File
  Output node in its scene's compositor and any scene strip its Sequencer
  renders, whose bounds and files a look could not hold (``admit_look``);
* a production render plans its own scene's File Output nodes
  (``job_render_scene``) and refuses the File Output nodes of a scene its
  Sequencer renders, whose files it could neither name nor send to its folder;
  the approval names those scenes (``admit_job``).

Every reachable node group, strip and scene is visited once, whatever the
nesting; past ``MAX_TREES`` groups, ``MAX_NODES`` nodes or ``MAX_STRIPS``
strips admission refuses, so an incomplete scan never authorizes a write.
Muted strips and nodes are skipped; strips are not filtered by frame range, so
a strip outside the rendered frames still counts (conservative). Refusals are
model-facing English. Main thread only; no ``bpy`` import.
"""

from .job_spec import JobRequestError

MAX_TREES = 4096
MAX_NODES = 100_000
MAX_STRIPS = 10_000
FILE_OUTPUT = "CompositorNodeOutputFile"


class _Budget:
    """The visits left across one admission's whole scan."""

    def __init__(self):
        self.trees, self.nodes, self.strips = set(), 0, 0

    def tree(self, tree):
        identity = tree.as_pointer() if hasattr(tree, "as_pointer") else id(tree)
        if identity in self.trees:
            return False
        self.trees.add(identity)
        if len(self.trees) > MAX_TREES:
            raise JobRequestError("The compositor has too many node groups to check for File Output nodes",
                                  code="LIMIT_EXCEEDED")
        return True

    def node(self):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise JobRequestError("The compositor has too many nodes to check for File Output nodes",
                                  code="LIMIT_EXCEEDED")

    def strip(self):
        self.strips += 1
        if self.strips > MAX_STRIPS:
            raise JobRequestError("The Sequencer has too many strips to check what its render reaches",
                                  code="LIMIT_EXCEEDED")


def file_outputs(tree, budget=None):
    """Whether ``tree`` or a group it uses runs an unmuted File Output node; shared groups and cycles count once."""
    budget = budget or _Budget()
    pending = [tree] if tree is not None else []
    while pending:
        current = pending.pop()
        if not budget.tree(current):
            continue
        for node in current.nodes:
            budget.node()
            if node.bl_idname == FILE_OUTPUT and not node.mute:
                return True
            child = getattr(node, "node_tree", None)
            if child is not None:
                pending.append(child)
    return False


def compositor_writes(scene, budget=None):
    """Whether a render of ``scene`` runs a File Output node of its own compositor."""
    if not getattr(scene.render, "use_compositing", False):
        return False
    return file_outputs(getattr(scene, "compositing_node_group", None), budget)


def _key(scene):
    """A scene's identity: Python wrappers of one data-block are distinct objects."""
    return getattr(scene, "session_uid", None) or id(scene)


def _strips(scene):
    if not getattr(scene.render, "use_sequencer", False):
        return ()
    editor = getattr(scene, "sequence_editor", None)
    return tuple(getattr(editor, "strips_all", ())) if editor is not None else ()


def strip_scenes(scene, budget=None):
    """``[(scene, renders_its_camera)]`` that an active Sequencer of ``scene`` renders, each once, in strip order.

    Camera input renders that scene (its compositor included); Sequencer input
    renders that scene's own strips, which are followed in turn. Blender never
    renders the bound scene from one of its own strips.
    """
    budget = budget or _Budget()
    found, seen, pending = [], {_key(scene)}, [scene]
    while pending:
        for strip in _strips(pending.pop(0)):
            budget.strip()
            other = getattr(strip, "scene", None) if strip.type == "SCENE" else None
            if other is None or strip.mute or _key(other) in seen:
                continue
            seen.add(_key(other))
            camera = getattr(strip, "scene_input", "CAMERA") != "SEQUENCER"
            found.append((other, camera))
            if not camera:
                pending.append(other)
    return found


def admit_look(scene):
    """Refuse (JobRequestError) a look that would write a file of its own or render another scene."""
    budget = _Budget()
    if compositor_writes(scene, budget):
        raise JobRequestError("A compositor File Output node would write outside the job's output; "
                              "mute it or turn off Compositing", code="UNSUPPORTED")
    others = strip_scenes(scene, budget)
    if others:
        names = ", ".join(other.name for other, _camera in others[:4])
        raise JobRequestError(
            "The Sequencer renders other scenes (%s) with their own render settings and outputs, which a look cannot "
            "bound. Turn off Sequencer in the Output properties' Post Processing to look at this scene's camera, or "
            "render it as a job the person approves (angelo_job_start RENDER_STILL)" % names, code="UNSUPPORTED")


def admit_job(scene):
    """The names of the other scenes a production render of ``scene`` renders; refuses their File Output nodes."""
    budget, names = _Budget(), []
    for other, camera in strip_scenes(scene, budget):
        if camera and compositor_writes(other, budget):
            raise JobRequestError(
                "The Sequencer renders the scene %s, whose compositor has a File Output node that would write outside "
                "the job's output. Mute that node, turn off Compositing in %s, or mute its scene strip"
                % (other.name, other.name), code="UNSUPPORTED")
        names.append(other.name)
    return names
