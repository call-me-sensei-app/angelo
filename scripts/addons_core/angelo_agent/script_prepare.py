# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the executor does around a reviewed script, beside running it (``blender_tools``, angelo_execute_python).

* **A parent's world matrix read before Blender evaluated its move** (``ParentInverse``). The idiom
  ``child.matrix_parent_inverse = parent.matrix_world.inverted()`` keeps a child where it is, but ``matrix_world`` is
  only updated when Blender evaluates the scene: right after ``parent.location = ...`` it is the parent's old matrix,
  and every child lands offset by the move (a car's parts left a metre off their body, doc/angelo/performance.rst,
  "Colours a request states"). Before each such statement the script runs a host call that evaluates the view layer
  only when that parent's matrix is stale (``_stale``), so the statement reads the parent's current matrix, as the
  script's author meant; a fresh parent is left alone and the statement runs as written. The lines where it evaluated
  are named in the result (``evaluated_before_parent_inverse``), with ``kit.parent`` as the call that parents in place.
It changes nothing else a script does, and is skipped, never failing the script, when it cannot apply.
"""

import ast

from .script_errors import SCRIPT_FILE

FRESH = "__angelo_evaluated_parent__"   # The host call the parent-inverse statements are preceded by.
INVERTS = ("inverted", "inverted_safe")
NOTE = ("The parent had moved since Blender last evaluated it, so the host evaluated the scene before these lines and "
        "each inverse is the parent's current matrix. kit.parent(child, parent) parents in place.")


def _simple(node):
    """A name, attributes of one and constant subscripts: reading it again has no effect of its own."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Constant):
            return False
        node = node.value
    return isinstance(node, ast.Name)


def _parent_of(statement):
    """The parent expression of ``<child>.matrix_parent_inverse = <parent>.matrix_world.inverted()``, else None."""
    if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1):
        return None
    target, value = statement.targets[0], statement.value
    if not (isinstance(target, ast.Attribute) and target.attr == "matrix_parent_inverse"):
        return None
    if not (isinstance(value, ast.Call) and not value.args and not value.keywords
            and isinstance(value.func, ast.Attribute) and value.func.attr in INVERTS):
        return None
    world = value.func.value
    if isinstance(world, ast.Attribute) and world.attr == "matrix_world" and _simple(world.value):
        return world.value
    return None


class _Precede(ast.NodeTransformer):
    """Puts ``FRESH(parent, line)`` before every parent-inverse statement, in whatever block it is."""

    def __init__(self):
        self.count = 0

    def generic_visit(self, node):
        super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if not isinstance(statements, list):
                continue
            out = []
            for statement in statements:
                parent = _parent_of(statement)
                if parent is not None:
                    call = ast.Expr(ast.Call(ast.Name(FRESH, ast.Load()),
                                             [_load(parent), ast.Constant(statement.lineno)], []))
                    out.append(ast.copy_location(call, statement))
                    self.count += 1
                out.append(statement)
            setattr(node, field, out)
        return node


def _load(node):
    """A copy of an expression for reading."""
    copy = ast.parse(ast.unparse(node), mode="eval").body
    return ast.copy_location(copy, node)


def _stale(obj):
    """Whether ``obj.matrix_world`` may not be what Blender would evaluate now. Exact for an object (and parents)
    without animation, drivers or constraints and parented to an object; anything else counts as stale, so the scene
    is evaluated: never wrong, at most a little slower."""
    expected = _basis_world(obj, 0)
    if expected is None:
        return True
    world = obj.matrix_world
    return any(abs(world[r][c] - expected[r][c]) > 1e-6 for r in range(4) for c in range(4))


def _basis_world(obj, depth):
    if depth > 64 or obj.constraints or (obj.animation_data is not None and (
            obj.animation_data.action is not None or obj.animation_data.drivers or obj.animation_data.nla_tracks)):
        return None
    if obj.parent is None:
        return obj.matrix_basis
    if obj.parent_type != "OBJECT":
        return None
    above = _basis_world(obj.parent, depth + 1)
    return None if above is None else above @ obj.matrix_parent_inverse @ obj.matrix_basis


class Prepared:
    """One script run's preparations (module docstring); ``compiled`` before it, ``after`` once it has run."""

    def __init__(self, bpy, view_layer):
        self._bpy, self._layer, self.evaluated = bpy, view_layer, []

    def compiled(self, code, namespace):
        """The code to run: as written, or with ``FRESH`` before each parent-inverse statement."""
        tree = ast.parse(code, SCRIPT_FILE, "exec")
        precede = _Precede()
        tree = precede.visit(tree)
        if not precede.count:
            return compile(code, SCRIPT_FILE, "exec")
        namespace[FRESH] = self._evaluated_parent
        return compile(ast.fix_missing_locations(tree), SCRIPT_FILE, "exec")

    def _evaluated_parent(self, parent, line):
        try:
            if isinstance(parent, self._bpy.types.Object) and _stale(parent):
                self._layer.update()
                if line not in self.evaluated:
                    self.evaluated.append(line)
        except Exception:   # noqa: BLE001 - never the script's failure: its statement then runs as written.
            pass

    def after(self, code):
        """What the result says of these preparations."""
        out = {}
        if self.evaluated:
            out["evaluated_before_parent_inverse"] = {"lines": self.evaluated[:16], "note": NOTE}
        return out


def namespace(bpy, scene, kit, script_file=None):
    """The globals a reviewed script runs in. An approved script file (``ToolCall.script_file``) runs as that file,
    as Blender's Text editor or ``blender --python`` would: ``__name__`` "__main__" and its real ``__file__``, so its
    main guard runs (break-it review FR-M1); inline code keeps a name no guard matches."""
    names = {"bpy": bpy, "context": bpy.context, "scene": scene, "result": None, "kit": kit,
             "__name__": "__angelo_reviewed_script__"}
    if script_file is not None:
        names.update(__name__="__main__", __file__=script_file.path)
    return names
