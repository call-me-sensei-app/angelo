# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender Python names a script may still write from Blender 4.x that Blender 5.2 removed or renamed.

``tool_specs.validate_call`` reads every reviewed script against this table before it is approved or run, and
refuses one that would certainly raise on such a name, naming the 5.2 replacement. Without it the script ran until
that line raised, the host rolled the whole build back, and the next round wrote it again (doc/angelo/
agent_execution.rst, "Removed Blender API").

The table is closed and precise: every entry is a documented 4.x or 5.0 removal or rename, or a recorded raise, and
absent from Blender 5.2's RNA (``test_native_script_api_removed.py`` and ``test_blender_script_api_removed.py`` pin
both). A use is refused only where it certainly runs into the removed name:

* an assignment of the removed enum value as a literal (``sky.sky_type = 'NISHITA'``), never a value chosen at run
  time or compared;
* an attribute read or write of a removed name, or a call of a removed operator;
* the Bump node's ``inputs['Invert']`` on a name the script made with ``nodes.new('ShaderNodeBump')`` (compositor
  nodes do have an Invert input);

and never inside a ``try`` statement, nor under an ``if``, ``while`` or conditional expression whose test asks for the
name (``hasattr``, ``in``, or the name as text): scripts that probe for either spelling keep running as before.
"""

import ast

# The same two 5.2 sky types as Blender's own error (bpy_angelo_renamed_api.cc) and the result note (script_api_notes).
SKY = ("use 'SINGLE_SCATTERING' (Blender 5.0 renamed 'NISHITA' to it, the same model: files saved with 'NISHITA' open "
       "with it); the new default is 'MULTIPLE_SCATTERING'")
# (attribute, literal value) -> the 5.2 value.
VALUES = {
    ("sky_type", "NISHITA"): SKY,
    ("engine", "BLENDER_EEVEE_NEXT"): "use 'BLENDER_EEVEE' (Blender 5.0 renamed EEVEE Next back to EEVEE)",
}
LEAVE_OUT = "leave it out: an EEVEE Legacy setting, removed in Blender 4.2 with nothing in its place"
# Attribute -> what to do in Blender 5.2 instead. None of these names is a property or function of any 5.2 RNA type.
ATTRIBUTES = {
    "dust_density": "use aerosol_density (renamed in Blender 5.0)",
    "use_auto_smooth": "use mesh.set_sharp_from_angle(angle=...) or the Smooth by Angle modifier (removed in Blender 4.1)",
    "auto_smooth_angle": "use mesh.set_sharp_from_angle(angle=...) (removed in Blender 4.1)",
    "calc_normals": "leave it out: normals are always current (removed in Blender 4.0)",
    "calc_normals_split": "read mesh.corner_normals, always current (removed in Blender 4.1)",
    "free_normals_split": "leave it out: corner normals are always current (removed in Blender 4.1)",
    "use_ssr": "use scene.eevee.use_raytracing (screen-space reflections were removed in Blender 4.2)",
    "use_ssr_refraction": "use scene.eevee.use_raytracing (screen-space refraction was removed in Blender 4.2)",
    "use_bloom": "use the compositor's Glare node (EEVEE's bloom was removed in Blender 4.2)",
    "use_gtao": LEAVE_OUT,
    "use_soft_shadows": LEAVE_OUT,
    "shadow_cube_size": LEAVE_OUT,
    "shadow_cascade_size": LEAVE_OUT,
}
# bpy.ops path -> its 5.2 operator.
OPERATORS = {
    "export_scene.obj": "use bpy.ops.wm.obj_export (since Blender 4.0)",
    "import_scene.obj": "use bpy.ops.wm.obj_import (since Blender 4.0)",
    "export_mesh.stl": "use bpy.ops.wm.stl_export (since Blender 4.2)",
    "import_mesh.stl": "use bpy.ops.wm.stl_import (since Blender 4.2)",
    "export_mesh.ply": "use bpy.ops.wm.ply_export (since Blender 4.0)",
    "import_mesh.ply": "use bpy.ops.wm.ply_import (since Blender 4.0)",
    "wm.collada_export": "use bpy.ops.wm.usd_export or bpy.ops.export_scene.gltf (Collada was removed in Blender 5.0)",
    "wm.collada_import": "use bpy.ops.wm.usd_import or bpy.ops.import_scene.gltf (Collada was removed in Blender 5.0)",
}
BUMP = "ShaderNodeBump"
BUMP_INVERT = "set node.invert = True: the Bump node's invert is a property, not an input"
MAX_LISTED = 5


def _dotted(node):
    """``a.b.c`` for a chain of names and attributes, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id] + parts[::-1])
    return None


def _bump_names(tree):
    """Names the script binds to ``<nodes>.new('ShaderNodeBump')`` (or ``type=``)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "new":
                kinds = [arg.value for arg in call.args[:1] if isinstance(arg, ast.Constant)] + [
                    keyword.value.value for keyword in call.keywords
                    if keyword.arg == "type" and isinstance(keyword.value, ast.Constant)]
                if BUMP in kinds:
                    names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


def _asks_for(test, words):
    """Whether a condition probes for one of ``words``: hasattr, ``in``, or one of them as text."""
    for node in ast.walk(test):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("hasattr", "getattr"):
            return True
        if isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            return True
        if isinstance(node, ast.Constant) and node.value in words:
            return True
    return False


class _Reader(ast.NodeVisitor):
    def __init__(self, bumps):
        self.bumps, self.found, self.guards = bumps, [], []

    def _report(self, node, what, instead):
        self.found.append((node.lineno, "line %d: %s is not in Blender 5.2: %s" % (node.lineno, what, instead)))

    def _guarded(self, words):
        return any(_asks_for(test, words) for test in self.guards)

    def visit_Try(self, node):   # A script that catches a failure here decides for itself: never refused.
        return

    visit_TryStar = visit_Try

    def _branch(self, node, test, bodies):
        self.visit(test)
        self.guards.append(test)
        for body in bodies:
            for child in body if isinstance(body, list) else [body]:
                self.visit(child)
        self.guards.pop()

    def visit_If(self, node):
        self._branch(node, node.test, [node.body, node.orelse])

    visit_While = visit_If

    def visit_IfExp(self, node):
        self._branch(node, node.test, [node.body, node.orelse])

    def visit_Assign(self, node):
        value = node.value
        for target in node.targets:
            if isinstance(target, ast.Attribute) and isinstance(value, ast.Constant):
                instead = VALUES.get((target.attr, value.value))
                if instead and not self._guarded({target.attr, value.value}):
                    self._report(node, "%s = %r" % (target.attr, value.value), instead)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        instead = ATTRIBUTES.get(node.attr)
        if instead and not self._guarded({node.attr}):
            self._report(node, "the attribute %s" % node.attr, instead)
        self.generic_visit(node)

    def visit_Call(self, node):
        path = _dotted(node.func)
        if path and path.startswith("bpy.ops.") and path[len("bpy.ops."):] in OPERATORS \
                and not self._guarded({path.rsplit(".", 1)[-1]}):
            self._report(node, path, OPERATORS[path[len("bpy.ops."):]])
        self.generic_visit(node)

    def visit_Subscript(self, node):
        holder = node.value
        if (isinstance(holder, ast.Attribute) and holder.attr == "inputs" and isinstance(holder.value, ast.Name)
                and holder.value.id in self.bumps and isinstance(node.slice, ast.Constant)
                and node.slice.value == "Invert" and not self._guarded({"Invert"})):
            self._report(node, "%s.inputs['Invert']" % holder.value.id, BUMP_INVERT)
        self.generic_visit(node)


def problems(code):
    """The lines of ``code`` that certainly use a name Blender 5.2 removed, each with its replacement, in order (at
    most MAX_LISTED); [] when there are none or the code does not parse (its syntax error is reported elsewhere)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    reader = _Reader(_bump_names(tree))
    reader.visit(tree)
    return [text for _line, text in sorted(set(reader.found))][:MAX_LISTED]


def refusal(found):
    """The validation error for a script with removed names: nothing has run."""
    return ("This script uses Blender Python that Blender 5.2 removed, so it was not run: " + "; ".join(found)
            + ". Correct those lines and send the script again.")
