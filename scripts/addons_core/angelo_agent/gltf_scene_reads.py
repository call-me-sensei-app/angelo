# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Blender's glTF exporter reads from the scene itself, for given exporter options. Main thread, read-only.

An edit of a scene setting updates only the scene, which ``scene_changes.content`` ignores, so a live export that
follows the scene (the web preview, the engine link) keys on what its exporter reads there besides the frame,
camera, World and visible names (``scene_changes.scene_key``). ``settings(scene, view_layer, options)`` is that
value for the options a caller passes to ``export_scene.gltf``; an option left out has the operator's default
(``DEFAULTS``, held to the operator by ``test_blender_engine_link_key``). Read off ``io_scene_gltf2.blender.exp``:

* always: the glTF scene's name and the game physics format (``io_scene_gltf2.angelo_physics``);
* ``export_lights``: the engine (Blender's EEVEE lights convert differently);
* ``export_cameras``: the render size and pixel aspect (a camera's aspect ratio and field of view);
* ``export_animations``: the frame rate and whether the NLA is being tweaked (the frame range is the shared key's);
* ``export_extras``: the scene's custom and add-on properties, as the exporter itself writes them (``extras``);
* ``use_active_collection``: the view layer's active collection.

An object's own settings (its render visibility for ``use_renderable``, its custom properties for ``export_extras``)
are not here: an edit of them updates the object with a transform or data flag, which ``scene_changes.content``
counts (``test_blender_engine_link_key`` sweeps an object's switches to hold it).

Cheap: a few property reads per update, more only where an option makes the exporter read more.
"""

# The operator's defaults for the options read here (``io_scene_gltf2.ExportGLTF2``).
DEFAULTS = {"export_lights": False, "export_cameras": False, "export_animations": True, "export_extras": False,
            "use_active_collection": False, "use_active_collection_with_nested": True}
SCENE_FIELDS = ("name", "angelo_physics_format")
LIGHT_FIELDS = ("engine",)
CAMERA_FIELDS = ("resolution_x", "resolution_y", "pixel_aspect_x", "pixel_aspect_y")
ANIMATION_RENDER_FIELDS = ("fps", "fps_base")
ANIMATION_SCENE_FIELDS = ("is_nla_tweakmode",)


def _option(options, name):
    return options.get(name, DEFAULTS[name])


def extras(scene):
    """The scene's extras as the exporter writes them (its own ``generate_extras``)."""
    from io_scene_gltf2.blender.com.extras import generate_extras
    return generate_extras(scene)


def settings(scene, view_layer, options):
    """Everything the glTF exporter run with ``options`` reads from ``scene`` (and ``view_layer``) itself, as one
    comparable value."""
    render = scene.render
    read = [tuple(getattr(scene, name, None) for name in SCENE_FIELDS)]
    if _option(options, "export_lights"):
        read.append(tuple(getattr(render, name, None) for name in LIGHT_FIELDS))
    if _option(options, "export_cameras"):
        read.append(tuple(getattr(render, name, None) for name in CAMERA_FIELDS))
    if _option(options, "export_animations"):
        read.append(tuple(getattr(render, name, None) for name in ANIMATION_RENDER_FIELDS)
                    + tuple(getattr(scene, name, None) for name in ANIMATION_SCENE_FIELDS))
    if _option(options, "export_extras"):
        read.append(("extras", extras(scene)))
    if _option(options, "use_active_collection") and view_layer is not None:
        active = view_layer.active_layer_collection
        read.append(("collection", active.collection if active is not None else None,
                     _option(options, "use_active_collection_with_nested")))
    return tuple(read)
