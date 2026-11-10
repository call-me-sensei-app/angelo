# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scene's look as the web carries it: colour management read from Blender, written as three.js settings.

``facts(scene)`` reads the view transform, look, exposure, gamma, curves, white balance and display device, and
the three.js tone mapping nearest the view and look (``web_tone``, measured with Blender's own OpenColorIO
configuration). ``web_compat`` turns what doesn't carry over into findings. ``extras(look, world, environment)``
is the ``angelo_look`` object written into the exported glTF scene's ``extras``, for the preview page and for a
developer's own three.js page (doc/angelo/web_export.rst has the schema). Main thread, read-only.
"""

import os

from . import web_tone

VERSION = 1


def _config_path():
    """The OpenColorIO configuration Blender uses: ``$OCIO`` (Blender exports its bundled one there), else bundled."""
    import bpy
    path = os.environ.get("OCIO", "")
    if path and os.path.isfile(path):
        return path
    folder = bpy.utils.system_resource("DATAFILES", path="colormanagement") or ""
    return os.path.join(folder, "config.ocio")


def settings(scene):
    """The scene's colour management as the export reads it: every value ``facts`` depends on, read from the scene
    and nothing else. The live preview keys on it (``web_facts.scene_settings``), so a field read here is followed
    by the preview without another change."""
    view = scene.view_settings
    return {"view_transform": view.view_transform, "look": view.look if view.look not in ("", "None") else None,
            "exposure": float(view.exposure), "gamma": float(view.gamma), "curves": bool(view.use_curve_mapping),
            "white_balance": bool(getattr(view, "use_white_balance", False)),
            "display": scene.display_settings.display_device}


def facts(scene):
    """The scene's colour management and the three.js settings nearest it. Reads the scene only through
    ``settings``."""
    read = settings(scene)
    config = _config_path()
    mapping = web_tone.measure(config, read["view_transform"], read["look"],
                               web_tone.web_display(config, read["display"], read["view_transform"]))
    readable = mapping is not None
    if mapping is None:
        mapping = {"tone_mapping": web_tone.FALLBACK[0], "output": web_tone.FALLBACK[1], "difference": None}
    return dict(read, readable=readable, tone_mapping=mapping["tone_mapping"], output=mapping["output"],
                difference=mapping["difference"])


def extras(look, world, environment, standin=None):
    """The ``angelo_look`` scene extras: three.js renderer and scene settings, and where they came from.

    ``world`` is ``web_world.plan``'s description; ``environment`` its equirect (``web_environment``), with
    ``data`` the Radiance bytes the exporter stores in a bufferView (``bufferView`` in the file), or None for a
    world that needs none, one still being made (``world["status"]`` "pending") or one that failed.

    ``standin`` (the live preview only): the environment the page shows now, as an earlier call wrote it (bytes
    where the bufferView goes). A picture still being made is written as that environment marked ``pending``, so a
    page loaded during the bake shows what the page showed before it; with none, ``{"type": "pending"}``."""
    result = {
        "version": VERSION,
        "toneMapping": look["tone_mapping"],
        "toneMappingExposure": web_tone.exposure(look["exposure"]),
        "outputColorSpace": look["output"],
        "blender": {"viewTransform": look["view_transform"], "look": look["look"] or "None",
                    "exposure": round(look["exposure"], 4), "gamma": round(look["gamma"], 4),
                    "display": look["display"]},
        "background": bool(world.get("background")),
    }
    kind = world["kind"]
    if kind == "COLOR":
        result["environment"] = {"type": "color", "color": [round(value, 6) for value in world["color"]],
                                 "intensity": round(world["intensity"], 6)}
    elif kind in ("IMAGE", "BAKE") and environment is not None:
        result["environment"] = {"type": "equirect", "mimeType": "image/vnd.radiance",
                                 "bufferView": environment["data"], "width": environment["width"], "height": environment["height"],
                                 "intensity": round(world.get("intensity", 1.0), 6),
                                 "source": "hdri" if kind == "IMAGE" else "baked"}
    elif kind in ("IMAGE", "BAKE") and world.get("status") == "pending":
        # The live preview publishes it once it is made; meanwhile the World the page already shows stands in.
        result["environment"] = dict(standin, pending=True) if standin else {"type": "pending"}
    else:
        result["environment"] = None   # No world: Blender renders no light from it and a black background.
    return {"angelo_look": result}
