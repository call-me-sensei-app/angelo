# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Web shaders: the built-ins, a shader file's uniforms and the glTF extras a material carries. No Blender.

A material can carry a web shader (``web_shader``): one of the built-ins below or a GLSL file in the project, and
its uniforms. The web export writes ``{"angelo_web_shader": ...}`` into that material's glTF ``extras``, which
other tools ignore; the live preview swaps in the shader. In Blender the material's nodes stay as the stand-in.

A shader file is a GLSL fragment shader (``.glsl``, ``.frag``): Angelo declares the varyings and the uniforms it
binds (``ANGELO_DECLARATIONS``), the file declares its own ``uniform float`` and ``uniform vec3`` values, which the
material's panel edits (every vec3 is a colour; ``// <min> <max>`` gives a float's slider range, ``= <value>`` its
default). The file ends with its ``main()``'s closing brace, before which Angelo adds Three.js's tone mapping and
colour space conversion. An optional ``#pragma angelo vertex`` line starts a vertex shader section after the
fragment shader, which replaces Angelo's (``uv``, ``normal``, ``position`` and the usual matrices are declared).
Three.js TSL files run only on its WebGPU renderer, which the preview does not use yet: TSL arrives in 1.1
(doc/angelo/web_export.rst).
"""

import os
import re

FLOAT, COLOR = "FLOAT", "COLOR"
BUILTINS = {
    # kind -> ordered uniforms: (name, type, default, minimum, maximum)
    "TOON": (("base_color", COLOR, (0.8, 0.5, 0.35), 0.0, 1.0), ("shadow_color", COLOR, (0.25, 0.16, 0.2), 0.0, 1.0),
             ("bands", FLOAT, 3.0, 2.0, 8.0), ("outline", FLOAT, 0.25, 0.0, 1.0)),
    "RIM": (("base_color", COLOR, (0.08, 0.1, 0.14), 0.0, 1.0), ("rim_color", COLOR, (0.17, 0.77, 0.75), 0.0, 1.0),
            ("rim_power", FLOAT, 2.5, 0.5, 8.0), ("rim_strength", FLOAT, 1.5, 0.0, 5.0)),
    "HOLOGRAM": (("color", COLOR, (0.25, 0.85, 1.0), 0.0, 1.0), ("lines", FLOAT, 120.0, 10.0, 400.0),
                 ("speed", FLOAT, 1.0, 0.0, 5.0), ("opacity", FLOAT, 0.55, 0.0, 1.0), ("flicker", FLOAT, 0.15, 0.0, 1.0)),
}
KINDS = ("NONE",) + tuple(BUILTINS) + ("FILE",)
GLSL_SUFFIXES = (".glsl", ".frag", ".fs")
TSL_SUFFIXES = (".js", ".mjs", ".ts")
MAX_SOURCE = 256 * 1024
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESERVED = frozenset({"uTime", "map", "uHasMap", "uResolution", "uLightDirection", "vUv", "vNormal", "vViewPosition",
                      "vWorldPosition"})
_UNIFORM = re.compile(r"^\s*uniform\s+(float|vec3)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*(?://\s*(.*))?$", re.M)
_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# What Angelo declares before a file's fragment shader, and the vertex shader it uses unless the file has one.
ANGELO_DECLARATIONS = """uniform float uTime;
uniform sampler2D map;
uniform float uHasMap;
uniform vec2 uResolution;
uniform vec3 uLightDirection;
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec3 vWorldPosition;
"""


def language(path):
    """"GLSL", "TSL" or None for a shader file's name."""
    lowered = path.lower()
    if lowered.endswith(GLSL_SUFFIXES):
        return "GLSL"
    if lowered.endswith(TSL_SUFFIXES):
        return "TSL"
    return None


MAX_INCLUDE_DEPTH = 16
_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"\n]+)"\s*(?://.*)?$')


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def expand_includes(path, root, read=None):
    """A GLSL file with its ``#include "file.glsl"`` lines replaced by those files' text, recursively.

    An include is a quoted path relative to the including file's folder. It must stay inside ``root`` (the project
    folder) after links are resolved, name a GLSL file and exist; a file already pasted is pasted once; a cycle, a
    chain deeper than ``MAX_INCLUDE_DEPTH`` or an expansion over ``MAX_SOURCE`` is refused. An ``#include`` in a
    comment is left alone. Returns {"source", "files" (every file read, first visit order), "problem", "detail"};
    on a problem ``source`` is None and ``detail`` names the file relative to ``root`` and the line, never a path
    outside it."""
    read = read or _read
    root = os.path.realpath(root)
    files, pasted, total = [], set(), [0]

    def name_of(real):
        return os.path.relpath(real, root).replace(os.sep, "/") if _inside(real, root) else os.path.basename(real)

    class Refused(Exception):
        def __init__(self, problem, detail):
            super().__init__(detail)
            self.problem, self.detail = problem, detail

    def expand(real, stack):
        files.append(real)
        pasted.add(real)
        try:
            text = read(real)
        except (OSError, UnicodeError):
            raise Refused("unreadable", "%s can't be read" % name_of(real)) from None
        total[0] += len(text)
        if total[0] > MAX_SOURCE:
            raise Refused("too_large", "the shader and its includes are over %d KB" % (MAX_SOURCE // 1024))
        lines, block = text.splitlines(keepends=True), False
        out = []
        for number, line in enumerate(lines, 1):
            match = None if block else _INCLUDE.match(line)
            block = _block_after(line, block)
            if match is None:
                out.append(line)
                continue
            where = "line %d of %s" % (number, name_of(real))
            written = match.group(1)
            if os.path.isabs(written) or written.startswith(("/", "\\")) or ":" in written.split("/", 1)[0]:
                raise Refused("include_outside", where + ": the included file is outside the project folder")
            target = os.path.realpath(os.path.join(os.path.dirname(real), written))
            if not _inside(target, root):
                raise Refused("include_outside", where + ": the included file is outside the project folder")
            if language(target) != "GLSL":
                raise Refused("include_language", where + ": %s is not a GLSL file" % name_of(target))
            if not os.path.isfile(target):
                raise Refused("include_missing", where + ": %s doesn't exist" % name_of(target))
            if target in stack:
                raise Refused("include_cycle", where + ": %s includes itself through other files" % name_of(target))
            if len(stack) >= MAX_INCLUDE_DEPTH:
                raise Refused("include_depth", where + ": includes nest deeper than %d files" % MAX_INCLUDE_DEPTH)
            if target in pasted:
                continue
            body = expand(target, stack + (target,))
            out.append(body if body.endswith("\n") or not body else body + "\n")
        return "".join(out)

    real = os.path.realpath(path)
    try:
        source = expand(real, (real,))
    except Refused as refused:
        return {"source": None, "files": files, "problem": refused.problem, "detail": refused.detail}
    return {"source": source, "files": files, "problem": None, "detail": ""}


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read(MAX_SOURCE + 1)


def _block_after(line, block):
    """Whether a /* */ comment is still open after ``line`` (a // comment ends the line's code)."""
    index = 0
    while index < len(line):
        if block:
            end = line.find("*/", index)
            if end < 0:
                return True
            block, index = False, end + 2
            continue
        start, comment = line.find("/*", index), line.find("//", index)
        if start < 0 or (0 <= comment < start):
            return False
        block, index = True, start + 2
    return block


def file_uniforms(source):
    """The editable uniforms a GLSL file declares: [(name, type, default, minimum, maximum)], in file order.

    ``uniform vec3 tint; // color`` is a colour (default white); ``uniform float gain; // 0 4`` a float with that
    slider range (default its minimum, or 0 .. 1 and 0.5 without a comment); ``// 0 4 = 1.5`` also sets the
    default. Names Angelo binds itself are never editable."""
    found, seen = [], set()
    for kind, name, comment in _UNIFORM.findall(source):
        if name in RESERVED or name in seen:
            continue
        seen.add(name)
        if kind == "vec3":
            found.append((name, COLOR, (1.0, 1.0, 1.0), 0.0, 1.0))
            continue
        numbers = [float(value) for value in re.findall(_NUMBER, comment or "")]
        low, high = (numbers[0], numbers[1]) if len(numbers) >= 2 and numbers[1] > numbers[0] else (0.0, 1.0)
        default = numbers[2] if len(numbers) >= 3 else (low if len(numbers) >= 2 else 0.5)
        found.append((name, FLOAT, min(max(default, low), high), low, high))
    return found


def split_source(source):
    """(vertex section or None, fragment section) of a shader file."""
    marker = re.search(r"^\s*#pragma\s+angelo\s+vertex\s*$", source, re.M)
    if marker is None:
        return None, source
    return source[marker.end():], source[:marker.start()]


def extras(kind, uniforms, file=None):
    """The ``extras`` value of a web-shaded material: ``uniforms`` is {name: float or [r, g, b]}."""
    shader = {"kind": kind.lower(), "uniforms": {name: (list(value) if isinstance(value, (tuple, list)) else value)
                                                for name, value in uniforms.items()}}
    if kind == "FILE":
        shader["file"] = file
        shader["language"] = (language(file or "") or "GLSL").lower()
    return {"angelo_web_shader": shader}
