# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bound scene's allow-listed settings: handle, exact revision, read and stage copy.

Scene settings (render, sampling, colour management, frame range, active
camera, units) are scene-level state with no object revision. A program binds
them through the scene's document handle (``scn:<epoch>:<uid>``, the same form
as object handles) and ``settings_revision``: a digest of exactly the settings
``property_set`` can write (the common paths it reports, the proven Cycles
settings and every other path ``rna_rules`` admits under the scene's roots),
the World, view-layer visibility and markers, so a manual change to any of
them between observation and commit fails the program closed, while unrelated
scene edits do not. Main thread only; reads RNA, never evaluates or changes
the scene.
"""

import hashlib
import json
import math

from .action_properties import PROPERTY_PATHS, SCENE, holder_and_name, plain, value_kind
from .rna_rules import PROVEN_RUNTIME
from .scene_read_values import label

SCENE_PATHS = tuple(path for path, spec in PROPERTY_PATHS.items() if spec[0] == SCENE)
# 3: view-layer collection visibility joined the digest; 4: timeline markers; 5: every admitted Cycles setting;
# 6: every other scene setting property_set admits (generic_values).
REVISION_VERSION = 6
# The World's complete exact RNA (and its node tree) joins the revision, bounded.
MAX_WORLD_ITEMS = 100000
MAX_LISTED_IMAGES = 64


def scene_handle(scene):
    from . import scene_queries
    return scene_queries.object_handle(scene)


def _available(scene, path):
    try:
        holder, name = holder_and_name(scene, path)
    except (AttributeError, ValueError):
        return None
    return (holder, name) if holder.bl_rna.properties.get(name) is not None else None


def raw_values(scene):
    """{path: value} of every allow-listed scene setting this build has; camera as session UID."""
    values = {}
    for path in SCENE_PATHS:
        found = _available(scene, path)
        if found is None:
            continue
        value = getattr(*found)
        values[path] = (int(value.session_uid) if value is not None else None) if value_kind(path) == "object" else plain(value)
    return values


def proven_values(scene):
    """{path: value} of the admitted Python-defined settings the report leaves out, for the revision only.

    A setting a program may write is one a stale plan must be refused for:
    ``rna_rules.PROVEN_RUNTIME`` beyond the common paths ``raw_values`` reports.
    """
    values = {}
    for path in sorted(PROVEN_RUNTIME):
        found = None if path in PROPERTY_PATHS else _available(scene, path)
        if found is not None:
            values[path] = plain(getattr(*found))
    return values


# (segments, struct identifier, leaves) of every scene settings struct beyond the common paths, built once:
# what rna_rules admits is fixed by the build's RNA (Python-defined settings are refused or proven above).
_GENERIC_GROUPS = None
# Admitted scene roots another part of the binding covers (_generic_groups).
UNREVISIONED_ROOTS = frozenset({"world", "frame_current"})


def _generic_groups():
    global _GENERIC_GROUPS
    if _GENERIC_GROUPS is None:
        import bpy
        from .rna_rules import SCENE_ROOTS, writable_groups
        # The World is covered whole by world_state; the common paths by raw_values; the current frame by every
        # program's bound frame (program_context), so a timeline move stays a recoverable frame-only change.
        _GENERIC_GROUPS = tuple(writable_groups(bpy.types.Scene.bl_rna, SCENE_ROOTS - UNREVISIONED_ROOTS,
                                                frozenset(SCENE_PATHS)))
    return _GENERIC_GROUPS


def generic_values(scene):
    """{path: value} of every other scene setting ``property_set`` may write, for the revision only.

    ``rna_rules`` admits any writable setting under ``SCENE_ROOTS``
    (``render.border_min_x``, ``eevee.gi_diffuse_bounces``, ``sync_mode``), so
    the revision must cover them all or a stale binding would overwrite a
    manual change. One getattr per setting along a
    precomputed plan (about 280 settings); an object pointer is its session
    UID. A settings struct the scene does not have reads as None; one whose
    instance type differs from the plan is digested whole, so nothing it
    holds can change unseen.
    """
    values = {}
    for segments, struct, leaves in _generic_groups():
        holder = scene
        for segment in segments:
            holder = getattr(holder, segment, None)
            if holder is None:
                break
        if holder is None or holder.bl_rna.identifier != struct:
            key = ".".join(segments)
            if holder is None:
                values[key] = None
            else:
                values[key] = {"struct": holder.bl_rna.identifier, "rna": _whole(holder)}
            continue
        prefix = ".".join(segments) + "." if segments else ""
        for name in leaves:
            value = getattr(holder, name)
            if not (value is None or type(value) in _SCALARS):
                # An object pointer reads as its session UID; arrays and flag sets as lists.
                value = int(value.session_uid) if hasattr(value, "session_uid") else _finite(plain(value))
            elif type(value) is float and not math.isfinite(value):
                value = repr(value)  # The digest refuses NaN and infinity; their text still changes it.
            values[prefix + name] = value
    return values


_SCALARS = (bool, int, float, str)


def _whole(holder):
    """Complete bounded RNA of a settings struct whose type the plan does not know."""
    from .program_dependencies import _rna_values
    from .program_jobs import ProgramError
    from .scene_read_values import SceneQueryError
    try:
        return _rna_values(holder, budget=[MAX_WORLD_ITEMS, MAX_WORLD_ITEMS * 8])
    except ProgramError as error:
        raise SceneQueryError("Scene settings revision unavailable: a settings struct exceeds its bounded read") from error


def _finite(value):
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return repr(value) if type(value) is float and not math.isfinite(value) else value


def world_state(world):
    """Exact bounded state of a World and its node tree (identity plus complete RNA)."""
    if world is None:
        return None
    from .program_dependencies import _rna_values
    from .program_jobs import ProgramError
    from .scene_read_values import SceneQueryError
    budget = [MAX_WORLD_ITEMS, MAX_WORLD_ITEMS * 8]
    try:
        return {"uid": int(world.session_uid), "rna": _rna_values(world, budget=budget),
                "tree": _rna_values(world.node_tree, budget=budget) if world.node_tree is not None else None}
    except ProgramError as error:
        raise SceneQueryError("Scene settings revision unavailable: the World exceeds its bounded read") from error


def settings_revision(scene):
    """Exact digest of the allow-listed settings and the World (64 hex characters, like object revisions)."""
    from .action_visibility import layer_rows
    from .anim_markers import revision_rows
    values, world, layers, markers = raw_values(scene), world_state(scene.world), layer_rows(scene), revision_rows(scene)
    proven, generic = proven_values(scene), generic_values(scene)
    payload = json.dumps([REVISION_VERSION, int(scene.session_uid), values, world, layers, markers, proven, generic],
                         sort_keys=True, allow_nan=False, separators=(",", ":"))
    revision = hashlib.blake2b(payload.encode(), digest_size=32).hexdigest()
    from .recovery_evidence import scene_capture
    scene_capture(scene, revision, values, world, layers, markers, proven=proven, generic=generic)
    return revision


def images():
    """Loaded image datablocks an environment can use: IDs, names and sizes, never paths."""
    import bpy
    from .action_world import image_id
    listed = []
    for image in bpy.data.images:
        if len(listed) >= MAX_LISTED_IMAGES:
            break
        if image.source in {"FILE", "GENERATED", "TILED"} and image.type in {"IMAGE", "UV_TEST", "MULTILAYER"}:
            # No size: reading it loads an unloaded image file from disk.
            listed.append({"id": image_id(image), "name": label(image.name), "source": image.source,
                           "loaded": bool(image.has_data)})
    return listed


def observe(window):
    """Read-only ``angelo_scene_settings`` result for the bound scene."""
    from . import scene_queries
    scene = window.scene
    values = raw_values(scene)
    if values.get("camera") is not None:
        values["camera"] = scene_queries.object_handle(scene.camera)
    from .action_world import summary
    from .action_world_spec import WorldGraphError, world_graph
    world = scene.world
    image = None
    if world is not None:
        try:
            environment = world_graph(world)["environment"]
            image = environment.image if environment is not None else None
        except WorldGraphError:
            pass
    # "handle"/"revision" are the binding keys short identifiers shorten and resolve.
    return {"handle": scene_handle(scene), "name": label(scene.name), "revision": settings_revision(scene),
            "values": values, "unavailable_in_build": [path for path in SCENE_PATHS if path not in values],
            "world": summary(world, image), "world_users": world.users if world is not None else 0,
            "world_graph": world_graph_names(world), "world_shows": _world_shows(scene),
            "images": images(), **_markers(scene),
            "note": ("Bind scene edits with this handle and revision in an angelo_scene_program write_set; "
                     "property_set and world_configure take the scene handle as target. An environment or a TEX_IMAGE node uses an image "
                     "ID listed here. Values are the bound scene's current settings, not a render.")}


def _world_shows(scene):
    """What the World shows, where its sun is beside the Sun lights, and how bright it shows (world_surface)."""
    from . import world_surface
    try:
        return world_surface.shows(scene.world, getattr(scene.render, "engine", None), scene)
    except (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError, KeyError):
        return None  # Optional evidence: the settings read stands without it.


def _markers(scene):
    """Timeline markers for the read (marker_edit addresses them by name), only when there are any."""
    from .anim_markers import summary
    return {"markers": summary(scene)} if len(scene.timeline_markers) else {}


def world_graph_names(world, limit=48):
    """The World graph's node names and types and its links, which node_edit addresses (bounded)."""
    tree = getattr(world, "node_tree", None)
    if tree is None:
        return None
    return {"nodes": [[label(node.name), node.bl_idname] for node in list(tree.nodes)[:limit]],
            "links": [[label(link.from_node.name), link.from_socket.identifier, label(link.to_node.name),
                       link.to_socket.identifier] for link in list(tree.links)[:limit]],
            "truncated": len(tree.nodes) > limit or len(tree.links) > limit}


def execute(window, args):
    """Closed main-thread ``angelo_scene_settings`` dispatch: no cache or object scan."""
    from . import scene_queries
    from .scene_spec import validate_scene
    validate_scene("angelo_scene_settings", args)
    scene_queries._main_thread()
    return dict(observe(window), document_epoch=scene_queries._epoch, read_only=True)


def copy_settings(source, target, staged_objects):
    """Copy the allow-listed settings of the live ``source`` scene into the private stage scene.

    ``staged_objects`` maps live object pointers to their staged copies; an
    active camera that the program did not declare stays unset on the stage.
    Raises ValueError if a value cannot be represented on the stage scene.
    """
    for path in SCENE_PATHS:
        found, destination = _available(source, path), _available(target, path)
        if found is None or destination is None:
            continue
        value = getattr(*found)
        if value_kind(path) == "object":
            value = staged_objects.get(value.as_pointer()) if value is not None else None
        setattr(*destination, value)
        copied = getattr(*destination)
        if value_kind(path) != "object" and plain(copied) != plain(value):
            raise ValueError("The scene setting %s could not be staged exactly" % path)
