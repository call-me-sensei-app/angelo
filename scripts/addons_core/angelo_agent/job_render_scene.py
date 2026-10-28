# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scene's render views and compositor File Output nodes, as a render job needs them.

``plan`` reads what a render of the scene would write (the active views, how
the image format stores them, every unmuted File Output node in the compositor
and in the groups it uses) and returns the outputs ``job_render_outputs``
names for them, with the settings that send each file into the job's staging
folder (``Plan.settings``): the render's own path and view format, and each
File Output node's directory, file name and file extension. Those settings are
RNA paths from the scene, so ``job_settings`` records and restores them like
any other, also from a file saved while the job ran.

A stored path is never used: Blender expands ``{variables}`` in output paths,
so the job's own paths are written with their braces doubled (``escape``).
Refusals are model-facing English. Main thread only.
"""

from pathlib import Path

import bpy

from . import job_render_outputs as outputs
from .job_settings import scene_value
import re

from .job_spec import FORMATS, SCENE_FORMAT, JobRequestError, name_problem

STEREO_VIEWS = frozenset({"left", "right"})
MAX_GROUP_DEPTH = 8
FORMAT = "render.image_settings."
# What setting a job's image format can change besides the format itself: recorded so they are restored too.
FORMAT_DEPENDENTS = (FORMAT + "color_depth", FORMAT + "color_mode")


def escape(path):
    """A path as Blender's output path templates read it literally: ``{`` and ``}`` doubled."""
    return str(path).replace("{", "{{").replace("}", "}}")


def active_views(scene):
    """The views a render of ``scene`` writes, as ``(name, file suffix)``; empty without multi-view."""
    render = scene.render
    if not render.use_multiview:
        return []
    views = [(view.name, view.file_suffix) for view in render.views
             if view.use and (render.views_format == 'MULTIVIEW' or view.name in STEREO_VIEWS)]
    if not views:
        raise JobRequestError("Stereoscopy is on but none of its views is enabled; enable a view or turn "
                              "Stereoscopy off")
    return views


MULTILAYER = "OPEN_EXR_MULTILAYER"
# The scene's own file name, used as a frame pattern with the SCENE format when it is a plain stem (no folders,
# braces or variables): Blender appends a four-digit frame number when the name has no '#'.
SCENE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,90}(#{1,8})?$")


def views_format(scene, file_format, views):
    """How the job's image format stores the views: the person's own choice where that format allows it."""
    wanted = scene_value(scene, FORMAT + "views_format")
    if wanted == "MULTIVIEW" and file_format in ("OPEN_EXR", MULTILAYER) and views:
        return "MULTIVIEW"
    if wanted == "STEREO_3D" and scene.render.views_format == 'STEREO_3D' and {name for name, _ in views} == STEREO_VIEWS:
        if file_format == MULTILAYER:   # Blender writes no file at all then (image_save.cc, "Stereo 3D not supported").
            raise JobRequestError("Angelo writes no multilayer OpenEXR file for Stereo 3D views; set the views' "
                                  "format to Individual or Multi-View, or render another format")
        return "STEREO_3D"
    return "INDIVIDUAL"


def scene_format(scene):
    """``(file format, extension)`` the scene's own image settings write, as F12 would (raises for a video)."""
    image = scene.render.image_settings
    if getattr(image, "media_type", "IMAGE") == "VIDEO" or image.file_format not in outputs.EXTENSIONS:
        raise JobRequestError("The scene writes a video; a render job writes image files. Pass format, or set the "
                              "scene's output to an image format")
    if image.file_format == "JPEG2000" and image.jpeg2k_codec == "J2K":
        return image.file_format, outputs.JPEG2000_CODESTREAM
    return image.file_format, outputs.EXTENSIONS[image.file_format]


CODECS = {"OPEN_EXR": ("exr_codec",), "OPEN_EXR_MULTILAYER": ("exr_codec",), "TIFF": ("tiff_codec",),
          "PNG": ("compression",), "JPEG": ("quality",), "WEBP": ("quality",), "JPEG2000": ("jpeg2k_codec", "quality"),
          "CINEON": ("use_cineon_log",), "DPX": ("use_cineon_log",)}


def format_facts(scene):
    """The scene's own image settings a SCENE job writes: format, depth, colour mode and that format's codec."""
    image = scene.render.image_settings
    facts = {"file_format": image.file_format, "color_depth": image.color_depth, "color_mode": image.color_mode}
    for key in CODECS.get(image.file_format, ()):
        if hasattr(image, key):
            facts[key] = getattr(image, key)
    return facts


def scene_name(scene):
    """The scene's own frame pattern (the file name part of its output path) when it is a plain stem, else None."""
    stem = scene.render.filepath.replace("\\", "/").rsplit("/", 1)[-1]
    if not SCENE_NAME.fullmatch(stem):
        return None
    name = stem if "#" in stem else stem + "####"
    return None if name_problem(name) else name   # Never a device name or a name that becomes an extension.


def split_name(name):
    """``(prefix, digits)`` of a frame pattern ending in its '#' run."""
    stem = name.rstrip("#")
    return stem, len(name) - len(stem)


def file_output_nodes(scene):
    """``[(RNA path from the scene, node)]`` of the unmuted File Output nodes a render of ``scene`` runs."""
    tree = getattr(scene, "compositing_node_group", None)
    if not scene.render.use_compositing or tree is None:
        return []
    found, seen = [], set()

    def walk(tree, path, depth):
        if tree.as_pointer() in seen:
            return  # One group used twice: its nodes write the same files once.
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            if node.mute:
                continue
            node_path = '%s.nodes["%s"]' % (path, bpy.utils.escape_identifier(node.name))
            if node.bl_idname == "CompositorNodeOutputFile":
                if tree.library is not None or not tree.is_editable:
                    raise JobRequestError("The File Output node %s is in a linked node group, so a job can't send "
                                          "its files to the job's folder; mute it or make the group local" % node.name)
                found.append((node_path, node))
            elif getattr(node, "node_tree", None) is not None:
                if depth >= MAX_GROUP_DEPTH:
                    raise JobRequestError("The compositor nests node groups deeper than %d levels; a job can't "
                                          "follow its File Output nodes that far" % MAX_GROUP_DEPTH)
                walk(node.node_tree, node_path + ".node_tree", depth + 1)

    walk(tree, "compositing_node_group", 0)
    return found


def _extension(image_format, node):
    if getattr(image_format, "media_type", "IMAGE") == "VIDEO" or image_format.file_format not in outputs.EXTENSIONS:
        raise JobRequestError("The File Output node %s writes a video; a render job writes image files. Set its "
                              "format to an image or mute it" % node.name)
    if image_format.file_format == "JPEG2000" and image_format.jpeg2k_codec == "J2K":
        return outputs.JPEG2000_CODESTREAM
    return outputs.EXTENSIONS[image_format.file_format]


def describe_node(node, folder):
    """One File Output node as plain data for ``job_render_outputs.plan``."""
    multilayer = node.format.file_format == "OPEN_EXR_MULTILAYER"
    items = []
    for item, socket in zip(node.file_output_items, node.inputs):
        own = item.format if item.override_node_format and not multilayer else node.format
        if not multilayer and own.file_format == "OPEN_EXR_MULTILAYER":
            raise JobRequestError("The item %s of the File Output node %s overrides its format with a multi-layer "
                                  "file; a job can't name that file. Use the node's own format" % (item.name, node.name))
        items.append({"name": item.name, "extension": _extension(own, node), "views_format": own.views_format,
                      "linked": bool(socket.is_linked)})
    return {"folder": folder, "name": node.name, "multilayer": multilayer, "extension": _extension(node.format, node),
            "views_format": node.format.views_format, "items": items}


class Plan:
    """What one render job writes and how its scene is pointed at the job's folder."""

    def __init__(self, scene, file_format, name=None):
        if file_format == SCENE_FORMAT:   # The scene's own image settings, untouched, and its own file name.
            self.file_format, self.extension = scene_format(scene)
            name = name or scene_name(scene)
        else:
            self.file_format, self.extension = file_format, FORMATS[file_format]
        self.name = name   # A frame pattern ("sh010_v003.####"), or None: frame_ and four digits, as always.
        prefix, digits = split_name(name) if name else (outputs.MAIN_PREFIX, 4)
        self.views = active_views(scene)
        self.views_format = views_format(scene, self.file_format, self.views)
        self.nodes, taken = [], set()
        for path, node in file_output_nodes(scene):
            self.nodes.append((path, describe_node(node, outputs.folder_name(node.name, taken, prefix))))
        try:
            self.outputs = outputs.plan(self.extension, self.views, self.views_format,
                                        [description for _path, description in self.nodes], prefix, digits)
        except outputs.PlanError as error:
            raise JobRequestError(str(error)) from None

    @property
    def single(self):
        """One file a frame and no File Output node: the plain render a job has always written."""
        return len(self.outputs) == 1 and not self.nodes

    def settings(self, staging):
        """``(values, also_record)`` for ``Overrides.apply``: every file of this plan goes below ``staging``."""
        values = {FORMAT + "views_format": self.views_format,
                  "render.filepath": escape(Path(staging) / (self.name or outputs.MAIN_PREFIX))}
        for path, node in self.nodes:
            values.update({path + ".directory": escape(Path(staging) / node["folder"]),
                           path + ".file_name": outputs.LAYERS_NAME if node["multilayer"] else "",
                           path + ".use_file_extension": True})
        return values

    def check_paths(self, scene, frame):
        """Blender's own answer for the render's image files must be the planned names (raises JobRequestError)."""
        render = scene.render
        for output in self.outputs:
            if not output["main"]:
                continue
            view = output["labels"].get("view")
            written = Path(render.frame_path(frame=frame)).name
            if view:   # The render writes a view's file by its suffix before the extension of the whole name
                # (image_save.cc BKE_scene_multiview_view_filepath_get); frame_path(view=) inserts it earlier.
                suffix = output["suffix"]
                stem, dot, extension = written.rpartition(".")
                written = stem + suffix + dot + extension if dot else written + suffix
            if written != Path(outputs.file_name(output, frame)).name:
                raise JobRequestError("Angelo would name this render's files differently than the job planned; "
                                      "nothing was started")
