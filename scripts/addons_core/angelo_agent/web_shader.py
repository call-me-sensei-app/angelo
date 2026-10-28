# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A material's web shader: its saved settings (``Material.angelo_web_shader``) and what the export and preview read.

The settings are a built-in kind (``web_shader_spec.BUILTINS``) or a shader file, and its uniforms: floats and
colours the material's Web Shader panel edits (``web_shader_ui``). Choosing a built-in fills in its uniforms;
choosing a file reads the uniforms it declares, keeping the values of names it already had. The export writes the
settings into the material's glTF extras (``extras``); the preview also serves the file's text (``sources``) and
notices a uniform edit or a file save at once (``signature``). The material's nodes are never touched.
"""

import os

import bpy
from bpy.props import CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, PointerProperty, \
    StringProperty

from . import web_shader_spec as spec
from .ui_text import n_

KIND_ITEMS = (("NONE", n_("None"), n_("Export the material's nodes as they are")),
              ("TOON", n_("Toon"), n_("Flat bands of light with a dark outline edge")),
              ("RIM", n_("Rim Glow"), n_("A glowing edge where the surface turns away from the camera")),
              ("HOLOGRAM", n_("Hologram"), n_("Animated scan lines, flicker and a see-through glow")),
              ("FILE", n_("Shader File"), n_("A GLSL fragment shader file in the project")))


def _sync(settings, wanted):
    """Make ``settings.uniforms`` the ``wanted`` list, keeping the value of every name that stays the same type."""
    kept = {item.name: (item.kind, item.value, tuple(item.color)) for item in settings.uniforms}
    settings.uniforms.clear()
    for name, kind, default, low, high in wanted:
        item = settings.uniforms.add()
        item.name, item.kind, item.minimum, item.maximum = name, kind, low, high
        old = kept.get(name)
        if kind == spec.COLOR:
            item.color = old[2] if old and old[0] == kind else default
        else:
            item.value = old[1] if old and old[0] == kind else default


def file_path(settings, material):
    """The shader file's absolute path, or "" when none is set."""
    return bpy.path.abspath(settings.path, library=material.library) if settings.path else ""


def file_problem(settings, material):
    """Why the shader file can't be used, without reading it (the panel draws this): a code, or None."""
    path = file_path(settings, material)
    if not path or not os.path.isfile(path):
        return "missing"
    if spec.language(path) != "GLSL":
        return "tsl" if spec.language(path) == "TSL" else "language"
    try:
        return "too_large" if os.path.getsize(path) > spec.MAX_SOURCE else None
    except OSError:
        return "unreadable"


def read_source(settings, material):
    """(text, problem): the file's text, or a problem code (``file_problem``'s, or unreadable)."""
    problem = file_problem(settings, material)
    if problem:
        return None, problem
    path = file_path(settings, material)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(), None
    except (OSError, UnicodeError):
        return None, "unreadable"


def _kind_changed(settings, context):
    if settings.kind in spec.BUILTINS:
        _sync(settings, spec.BUILTINS[settings.kind])
    elif settings.kind == "FILE":
        read_uniforms(settings, settings.id_data)


def _path_changed(settings, context):
    if settings.kind == "FILE":
        read_uniforms(settings, settings.id_data)


def read_uniforms(settings, material):
    """Read the file's uniforms into ``settings``; returns the problem code, or None."""
    source, problem = read_source(settings, material)
    if source is not None:
        _sync(settings, spec.file_uniforms(source))
    return problem


class ANGELO_PG_web_uniform(bpy.types.PropertyGroup):
    kind: EnumProperty(items=((spec.FLOAT, "Number", ""), (spec.COLOR, "Color", "")), default=spec.FLOAT)
    value: FloatProperty(name="Value", precision=3)
    color: FloatVectorProperty(name="Color", subtype="COLOR", size=3, min=0.0, max=1.0, default=(1.0, 1.0, 1.0))
    minimum: FloatProperty(default=0.0)
    maximum: FloatProperty(default=1.0)


class ANGELO_PG_web_shader(bpy.types.PropertyGroup):
    kind: EnumProperty(name="Web Shader", items=KIND_ITEMS, default="NONE", update=_kind_changed,
                       description="What the browser shows for this material; Angelo keeps showing its nodes")
    path: StringProperty(name="Shader File", subtype="FILE_PATH", update=_path_changed,
                         description="A GLSL fragment shader (.glsl, .frag) in the project")
    uniforms: CollectionProperty(type=ANGELO_PG_web_uniform)


def settings_of(material):
    settings = getattr(material, "angelo_web_shader", None)
    return settings if settings is not None and settings.kind != "NONE" else None


def file_key(settings, material):
    """How the export names the shader file: relative to the .blend's folder, else its bare name (never a local
    path in a file meant to be shared)."""
    path = file_path(settings, material)
    if not path:
        return ""
    base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
    if base:
        relative = os.path.relpath(path, base)
        if not relative.startswith(".."):
            return relative.replace(os.sep, "/")
    return os.path.basename(path)


def uniform_values(settings):
    return {item.name: (tuple(round(v, 6) for v in item.color) if item.kind == spec.COLOR else round(item.value, 6))
            for item in settings.uniforms if spec.NAME.match(item.name)}


def extras(material):
    """The glTF extras of ``material``'s web shader, or None."""
    settings = settings_of(material)
    if settings is None:
        return None
    return spec.extras(settings.kind, uniform_values(settings), file_key(settings, material) or None)


def sources(materials):
    """{file key: GLSL text} for the web-shaded materials among ``materials`` whose file reads."""
    found = {}
    for material in materials:
        settings = settings_of(material)
        if settings is not None and settings.kind == "FILE":
            source, _problem = read_source(settings, material)
            if source is not None:
                found[file_key(settings, material)] = source
    return found


def signature(materials):
    """What a live reload watches: each web shader's kind, uniforms and its file's modification time."""
    parts = []
    for material in materials:
        settings = settings_of(material)
        if settings is None:
            continue
        stamp = None
        if settings.kind == "FILE":
            try:
                stamp = os.stat(file_path(settings, material)).st_mtime_ns
            except (OSError, ValueError):
                stamp = None
        parts.append((material.name, settings.kind, settings.path, stamp, tuple(sorted(uniform_values(settings).items()))))
    return tuple(parts)


CLASSES = (ANGELO_PG_web_uniform, ANGELO_PG_web_shader)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.angelo_web_shader = PointerProperty(type=ANGELO_PG_web_shader)


def unregister():
    del bpy.types.Material.angelo_web_shader
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
