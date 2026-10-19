# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every file a render job writes, known before it starts: per view and per compositor File Output slot.

No Blender dependency. ``job_render_scene`` reads a scene into plain data;
this module turns it into a plan of *outputs* and checks afterwards that
Blender wrote exactly what was planned. An output is one file per frame:

* the render's own image: one per active view, or one for the frame when the
  views are combined (anaglyph or side by side, or a multi-view EXR);
* one per linked item of a File Output node, per view when its format keeps
  the views apart; a multi-layer node writes one file holding all its items.

The job names every file itself, so nothing depends on a path or a file name
template stored in the scene: ``frame_`` for the render's image, and for a
File Output node a folder named after the node holding ``<item><frame>`` (or
``layers<frame>`` for a multi-layer node). Blender appends the frame number
(four digits at least, a minus sign before them) and a view's file suffix.
``sort_files`` fails closed on a file the plan does not name.

An output is a dict: ``prefix`` and ``suffix`` (around the frame number),
``extension``, ``main`` (the render's own image) and ``labels``: what it is
(``view`` or ``views``, ``node``, ``slot`` or ``layers``), which a finished
job reports with each file (``entries``).
"""

import re
import unicodedata

from .job_store import FRAME_MARK  # Stands for the frame number in the file pattern of an animation's output.

# What Blender appends for each image format a job can plan (``RenderSettings.file_extension``; the GUI suite
# compares this table with Blender's own answer). Video formats are refused.
EXTENSIONS = {"AVIF": ".avif", "BMP": ".bmp", "CINEON": ".cin", "DPX": ".dpx", "HDR": ".hdr", "IRIS": ".rgb",
              "JPEG": ".jpg", "JPEG2000": ".jp2", "OPEN_EXR": ".exr", "OPEN_EXR_MULTILAYER": ".exr", "PNG": ".png",
              "TARGA": ".tga", "TARGA_RAW": ".tga", "TIFF": ".tif", "WEBP": ".webp"}
JPEG2000_CODESTREAM = ".j2c"   # JPEG 2000 with its J2K codec.
MAIN_PREFIX = "frame_"
LAYERS_NAME = "layers"
MAX_OUTPUTS = 64               # Files per frame one job plans (views times slots).
# A view's file suffix and an item's name end up in file names, where "#" and braces mean a frame number and a
# path variable to Blender, and a separator would leave the folder.
_UNSAFE_IN_NAME = re.compile(r"[#{}/\\\x00-\x1f]")
_UNSAFE_IN_FOLDER = re.compile(r"[^A-Za-z0-9._-]+")


class PlanError(ValueError):
    """A scene whose outputs a job cannot name in advance; the message says what to change."""


def safe_part(text):
    """Whether ``text`` can be part of a file name the job predicts exactly."""
    return isinstance(text, str) and not _UNSAFE_IN_NAME.search(text)


def folder_name(node_name, taken):
    """A folder inside the job's output for one File Output node: its name made file-safe, unique among ``taken``."""
    stem = _UNSAFE_IN_FOLDER.sub("_", node_name).strip("._") or "FileOutput"
    if stem.lower().startswith(MAIN_PREFIX):  # Never the name of one of the render's own image files.
        stem = "node-" + stem
    name, number = stem[:48], 1
    while name.lower() in taken:
        number += 1
        name = "%s-%d" % (stem[:44], number)
    taken.add(name.lower())
    return name


def _output(prefix, extension, *, suffix="", main=False, **labels):
    return {"prefix": prefix, "suffix": suffix, "extension": extension, "main": main,
            "labels": {key: value for key, value in labels.items() if value}}


def _per_view(prefix, extension, views, views_format, **labels):
    """One output per view when the format keeps views apart, else one for all of them."""
    if views and views_format == "INDIVIDUAL":
        suffixes = [suffix for _name, suffix in views]
        if len(set(suffixes)) != len(suffixes) or not all(safe_part(suffix) for suffix in suffixes):
            raise PlanError("Each view needs its own file suffix without #, braces or slashes, so every view gets its "
                            "own file; change the views' File Suffix in the Stereoscopy settings")
        return [_output(prefix, extension, suffix=suffix, view=name, **labels) for name, suffix in views]
    return [_output(prefix, extension, views=[name for name, _suffix in views] if views else None, **labels)]


def plan(extension, views, views_format, nodes):
    """The outputs of one render: its own image first, then every File Output node's.

    ``views`` are the active views as ``(name, file suffix)``, empty without
    multi-view. ``nodes`` are ``job_render_scene``'s descriptions: ``folder``,
    ``name``, ``multilayer``, and ``items`` (``name``, ``extension``,
    ``views_format``, ``linked``). Raises PlanError for what cannot be named.
    """
    outputs = [dict(output, main=True) for output in _per_view(MAIN_PREFIX, extension, views, views_format)]
    for node in nodes:
        linked = [item for item in node["items"] if item["linked"]]
        if node["multilayer"]:
            if linked:  # Blender writes a multi-layer file only when one of its inputs is an image.
                outputs += _per_view("%s/%s" % (node["folder"], LAYERS_NAME), node["extension"], views,
                                     node["views_format"], node=node["name"],
                                     layers=[item["name"] for item in node["items"]])
            continue
        names = [item["name"] for item in linked]
        if len(set(names)) != len(names) or not all(name and safe_part(name) for name in names):
            raise PlanError("The File Output node %s needs a distinct name without #, braces or slashes for each "
                            "linked item, so every item gets its own file" % node["name"])
        for item in linked:
            outputs += _per_view("%s/%s" % (node["folder"], item["name"]), item["extension"], views,
                                 item["views_format"], node=node["name"], slot=item["name"])
    if len(outputs) > MAX_OUTPUTS:
        raise PlanError("This render would write %d files a frame; a job plans at most %d. Mute some File Output "
                        "nodes or render fewer views" % (len(outputs), MAX_OUTPUTS))
    names = {}
    for output in outputs:
        name = pattern(output)
        # Canonically equivalent spellings alias on normalization-insensitive filesystems as well.
        key = unicodedata.normalize("NFC", name).casefold()
        if key in names:
            raise PlanError("The render outputs %s and %s name the same file ignoring case or Unicode composition; "
                            "rename the File Output item or view suffix so every output has a distinct name "
                            "ignoring case and Unicode composition"
                            % (names[key], name))
        names[key] = name
    return outputs


def file_name(output, frame):
    """The file one output writes for one frame, relative to the job's output folder."""
    return "%s%.4d%s%s" % (output["prefix"], frame, output["suffix"], output["extension"])


def pattern(output):
    return output["prefix"] + FRAME_MARK + output["suffix"] + output["extension"]


def entries(outputs, frame=None):
    """What a job reports for each output: its ``file`` (a pattern with #### unless ``frame`` is given) and labels."""
    return [dict({"file": pattern(output) if frame is None else file_name(output, frame)}, **output["labels"])
            for output in outputs]


def signature(outputs):
    """What an approval covered: a queued job whose scene now plans other files is refused when it starts."""
    return tuple((output["prefix"], output["suffix"], output["extension"]) for output in outputs)


def main_names(outputs, start, end):
    """``{file name: frame}`` of the render's own image files (they sit at the top of the output folder)."""
    return {file_name(output, frame): frame for output in outputs if output["main"] for frame in range(start, end + 1)}


def sort_files(outputs, start, end, present):
    """Compare what a render wrote (``present``: relative posix paths) with its plan.

    Returns ``frames`` (the finished ones: every file of the render's own image
    exists, which Blender writes last), ``drop`` (files of a frame that did not
    finish), ``unexpected`` (files the plan does not name: nothing may be
    published) and ``silent`` (outputs without a file in any finished frame:
    Blender writes a File Output item only when its input is an image).
    """
    present, expected = set(present), {}
    for index, output in enumerate(outputs):
        for frame in range(start, end + 1):
            expected[file_name(output, frame)] = (index, frame)
    mains = [index for index, output in enumerate(outputs) if output["main"]]
    frames = [frame for frame in range(start, end + 1)
              if all(file_name(outputs[index], frame) in present for index in mains)]
    finished, wrote, drop = set(frames), set(), []
    for path in sorted(present & set(expected)):
        index, frame = expected[path]
        if frame in finished:
            wrote.add(index)
        else:
            drop.append(path)
    return {"frames": frames, "drop": drop, "unexpected": sorted(present - set(expected)),
            "silent": [index for index in range(len(outputs)) if index not in wrote]}
