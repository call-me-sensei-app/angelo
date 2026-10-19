# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripted drivers whose own Python would run when a scene renders or bakes.

``render_extensions`` asks render approval only when a render would actually
run add-on or script Python, so a Scripted driver counts only when all of
these hold:

* **It needs Python.** Unmuted, Scripted, and not a simple expression Blender
  evaluates natively (``is_simple_expression``), or it uses ``self``.
* **The render evaluates it.** A render evaluates the data-blocks its scene
  reaches through ID references: collections and objects, their data,
  materials, node groups, shape keys, constraint and modifier targets, the
  world, the compositor, background-set and Sequencer strip scenes, driver
  targets. ``evaluated`` follows ``bpy.data.user_map`` forward from the scene.
  Blender builds the render's dependency graph from the same references, so
  this can only over-approximate it (an object excluded from every view layer
  still counts), never miss a data-block: a driver of another scene's object
  does not count.
* **Its Python would run, and is not just arithmetic.** An expression that
  names only its driver variables, ``frame``, the builtins and functions
  Blender allows untrusted files (``all``, ``any``, ``len``, ``max``, ``min``,
  ``pow``, ``round``, ``sum``, ``bool``, ``float``, ``int``, ``bl_math``'s
  ``clamp``, ``lerp`` and ``smoothstep``, and ``math``) runs no one's code but
  Python's and Blender's: it does not count. Any other name counts when
  auto-execution is on. With it off, Blender evaluates an expression only when
  every name in its compiled code is a variable, one of those builtins or an
  entry of its driver namespace, none ``_``-prefixed, and its opcodes pass
  (``bpy_driver.cc``, ``bpy_driver_bytecode.cc``); anything else is blocked and
  never runs. So in that restricted mode only a name the namespace maps to
  something that does not ship with the application (a function an add-on put
  in ``bpy.app.driver_namespace``) counts: Blender lets it run.

Restricted mode is trusted only while auto-execution has stayed off since the
document loaded: Blender keeps an expression compiled while it was on and
runs it in full after it is switched off, until the document loads again
(``bpy_driver.cc`` checks the bytecode only when it compiles). ``restricted``
therefore remembers any time it saw auto-execution on, the preference's
message-bus subscription (``watch``) does the same between looks, and
``document_loaded`` (load_post) starts again. A binary without the native
flag (``_bpy._angelo_jobs.scripts_auto_execute``) counts as auto-execution
on, and a stand-in without ``user_map`` as every data-block evaluated; the
opcode check is not replicated (restricted mode may count an expression
Blender still blocks). All three are conservative.

Cost: one pass over the data-blocks' drivers; ``user_map`` and the reference
walk only when a driver needs Python. Main thread only; ``bpy`` is imported
where used.
"""

import math

# Names Blender lets a driver of an untrusted file use: Python's and Blender's arithmetic, nobody's own code.
SECURE = frozenset(name for name in dir(math) if not name.startswith("_")) | frozenset({
    "all", "any", "len", "max", "min", "pow", "round", "sum", "bool", "float", "int",
    "clamp", "lerp", "smoothstep"})
# Entries Blender itself puts in the driver namespace.
BLENDER_NAMESPACE = frozenset({"__builtins__", "bpy", "noise", "frame", "self", "depsgraph"})
# bpy.data collections whose data-blocks can carry drivers, and those with an embedded node tree of their own.
DATA = ("objects", "meshes", "curves", "hair_curves", "pointclouds", "volumes", "grease_pencils", "annotations",
        "lattices", "metaballs", "armatures", "cameras", "lights", "lightprobes", "speakers", "materials", "textures",
        "worlds", "node_groups", "shape_keys", "particles", "linestyles", "scenes", "cache_files", "movieclips", "masks")
EMBEDDED_TREES = frozenset({"materials", "worlds", "lights", "linestyles", "textures", "scenes"})
PREFERENCE = "use_scripts_auto_execute"

_compiled_in_full = True  # Whether a driver may hold an expression compiled with auto-execution on.
_watching = False  # Only a followed preference lets a load lower that again.
_OWNER = object()  # The message-bus subscription's owner.


def auto_execute():
    """Whether the document may run its Python now (``G_FLAG_SCRIPT_AUTOEXEC``); True when unknown."""
    try:
        from _bpy import _angelo_jobs
        return bool(_angelo_jobs.scripts_auto_execute())
    except (ImportError, AttributeError):
        return True


def restricted():
    """Whether every scripted driver runs in Blender's restricted mode: auto-execution off since the load."""
    global _compiled_in_full
    if auto_execute():
        _compiled_in_full = True
        return False
    return not _compiled_in_full


def document_loaded(*_args):
    """load_post: the loaded document's drivers compile afresh, under the current setting."""
    global _compiled_in_full
    _compiled_in_full = auto_execute() or not _watching


def _preference_changed():
    restricted()  # Remembers auto-execution on, even if it is switched off before the next look.


def watch():
    """Follow the auto-execution preference between looks (its update sets the flag at once)."""
    global _watching
    import bpy
    try:
        bpy.msgbus.subscribe_rna(key=(bpy.types.PreferencesFilePaths, PREFERENCE), owner=_OWNER, args=(),
                                 notify=_preference_changed, options={'PERSISTENT'})
    except (AttributeError, KeyError, TypeError, ValueError):
        return  # Unfollowed: restricted mode is never trusted, so every Python driver counts.
    _watching = True
    document_loaded()


def unwatch():
    global _watching, _compiled_in_full
    import bpy
    _watching, _compiled_in_full = False, True
    try:
        bpy.msgbus.clear_by_owner(_OWNER)
    except AttributeError:
        pass


def _needs_python(holder):
    animation = getattr(holder, "animation_data", None)
    for curve in animation.drivers if animation is not None else ():
        driver = curve.driver
        if not curve.mute and driver.type == "SCRIPTED" and (driver.use_self or not driver.is_simple_expression):
            yield curve, driver


def _candidates(data):
    """``(collection, owner, part, curve, driver)`` of every driver in the document that needs Python."""
    for collection in DATA:
        for owner in getattr(data, collection, ()):
            holders = [(owner, "")]
            tree = getattr(owner, "node_tree", None) if collection in EMBEDDED_TREES else None
            if tree is not None and getattr(tree, "is_embedded_data", False):
                holders.append((tree, " (node tree)"))
            for holder, part in holders:
                for curve, driver in _needs_python(holder):
                    yield collection, owner, part, curve, driver


def evaluated(data, scene):
    """The data-blocks ``scene`` reaches through ID references (it included), or None when that cannot be read."""
    user_map = getattr(data, "user_map", None)
    if user_map is None:
        return None
    forward = {}
    for target, users in user_map().items():
        for user in users:
            forward.setdefault(user, []).append(target)
    reached, pending = {scene}, [scene]
    while pending:
        for target in forward.get(pending.pop(), ()):
            if target not in reached:
                reached.add(target)
                pending.append(target)
    return reached


def _names(code):
    names = set(code.co_names)
    for constant in code.co_consts:
        if hasattr(constant, "co_names"):  # A comprehension or lambda inside the expression.
            names |= _names(constant)
    return names


def runs_code(driver, restricted_mode, namespace, shipped):
    """Whether evaluating ``driver`` runs code beyond arithmetic and Blender's own functions.

    ``shipped(value)`` says whether a namespace entry is code that ships with the application.
    """
    try:
        names = _names(compile(driver.expression, "<bpy driver>", "eval"))
    except (SyntaxError, ValueError, TypeError):
        return False  # Blender cannot compile it either: nothing runs.
    variables = {variable.name for variable in driver.variables}
    own = names - SECURE - variables - {"frame"}
    if driver.use_self:
        own.add("self")
    if not own:
        return False
    if not restricted_mode:
        return True
    allowed = set(namespace) | SECURE | variables | {"frame"}
    if any(name.startswith("_") or name not in allowed for name in names):
        return False  # Blocked: Blender evaluates nothing of it.
    return any(name in namespace and name not in BLENDER_NAMESPACE and not shipped(namespace[name]) for name in own)


def python_drivers(scene, shipped):
    """``[(identity, label)]`` of the drivers whose own Python a render of ``scene`` would run."""
    import bpy
    data = getattr(bpy, "data", None)
    candidates = list(_candidates(data))
    if not candidates:
        return []
    reached = evaluated(data, scene)
    restricted_mode = restricted()
    namespace = getattr(getattr(bpy, "app", None), "driver_namespace", None) or {}
    found = []
    for collection, owner, part, curve, driver in candidates:
        if reached is not None and owner not in reached:
            continue
        if not runs_code(driver, restricted_mode, namespace, shipped):
            continue
        index = "[%d]" % curve.array_index if curve.array_index else ""
        identity = [collection, getattr(owner, "session_uid", 0), part, curve.data_path, curve.array_index,
                    driver.expression, bool(driver.use_self)]
        found.append((identity, ("%s%s: %s%s" % (owner.name, part, curve.data_path, index))[:120]))
    return found
