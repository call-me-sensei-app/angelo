# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flows' Generate Video step: a video model (fal's, or Seedance on BytePlus), a prompt and up to two frames.

Its ``frames`` input takes the first frame and then the last, in link order (``flow_spec.INPUTS``), so an Image node
gives Image → Video and a Scene Capture with Framing "Scene camera" gives Camera → Video. Its options (length,
resolution, aspect ratio, audio) are the chosen model's own (``fal_models``), kept in the step's options JSON like
every generator's; a value the model doesn't take is never offered, and the run checks the rest
(``studio_agent_video.video_generation``). Built on ``flow_nodes``' generator base, as ``flow_character_nodes`` is.
"""

import json

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy.types import Node

from . import fal_models
from .flow_nodes import VIDEO_IDNAME, _GeneratorNode, _models
from .ui_text import iface_, n_

_items = {}   # Blender requires enum strings to stay referenced.


def _offer(node):
    """The chosen model's text-mode offer (the widest), or None before a model is chosen."""
    if not fal_models.is_video_model(node.model):
        return None
    return fal_models.capabilities(node.model, fal_models.TEXT)


def _options(node):
    try:
        value = json.loads(node.options_json or "{}")
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def _write(node, key, value):
    options = _options(node)
    options[key] = value
    node.options_json = json.dumps(options, sort_keys=True)


def _choice(key, plural):
    """An options-JSON value from the model's own list, as an enum (empty while no model is chosen)."""
    def items(self, context):
        offer = _offer(self)
        values = list(offer[plural]) if offer else []
        _items[(self.as_pointer(), key)] = entries = [(v, v, "") for v in values] or [("", iface_("Model default"), "")]
        return entries

    def get(self):
        offer = _offer(self)
        values = list(offer[plural]) if offer else []
        default = fal_models.defaults(self.model)[key] if offer else ""
        value = _options(self).get(key, default)
        return values.index(value) if value in values else (values.index(default) if default in values else 0)

    def set(self, index):
        offer = _offer(self)
        values = list(offer[plural]) if offer else []
        if 0 <= index < len(values):
            _write(self, key, values[index])
    return EnumProperty(name=key.title(), items=items, get=get, set=set)


def _get_duration(self):
    offer = _offer(self)
    value = _options(self).get("duration")
    return value if offer and value in offer["durations"] else (fal_models.defaults(self.model)["duration"] if offer else 5)


def _set_duration(self, value):
    offer = _offer(self)
    if offer:   # The nearest length the model takes (Veo: 4, 6 or 8 seconds).
        _write(self, "duration", min(offer["durations"], key=lambda d: (abs(d - value), d)))


def _get_audio(self):
    return bool(_options(self).get("audio", False))


def _set_audio(self, value):
    _write(self, "audio", bool(value))


class ANGELO_FlowGenerateVideoNode(_GeneratorNode, Node):
    """Generate a video clip with a video model"""
    bl_idname = VIDEO_IDNAME
    bl_label = "Generate Video"
    bl_icon = 'FILE_MOVIE'
    angelo_type = "GENERATE_VIDEO"
    prompt_hint = n_("Prompt: subject, motion and camera")
    model_choice: _models("VIDEO")
    duration: IntProperty(name="Length", description="Seconds of video; the price is per second", min=1, max=30,
                          get=_get_duration, set=_set_duration)
    resolution: _choice("resolution", "resolutions")
    aspect: _choice("aspect", "aspects")
    audio: BoolProperty(name="Audio", description="Generate a soundtrack (some models bill it extra)",
                        get=_get_audio, set=_set_audio)

    def _draw_options(self, layout):
        offer = _offer(self)
        if offer is None:
            return
        row = layout.row(align=True)
        row.prop(self, "duration")
        if offer["resolutions"]:
            row.prop(self, "resolution", text="")
        row = layout.row(align=True)
        if offer["aspects"]:
            row.prop(self, "aspect", text="")
        if offer["audio"]:
            row.prop(self, "audio", toggle=True)
        layout.label(text=iface_("Frames: first, then last (link order)"), icon='INFO', translate=False)


CLASSES = (ANGELO_FlowGenerateVideoNode,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
