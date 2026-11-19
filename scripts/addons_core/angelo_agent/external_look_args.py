# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's look (``angelo_look``) as the ``angelo_view`` call Angelo's agent would make. No Blender dependency.

An app asks for views by name (the six axes, ISO, ``perspective``, FIT, SCENE_CAMERA), by an orbit around the chosen
objects, by a scene camera's name or as a free camera, framed on objects named as the person sees them, at one of
``angelo_view``'s resolutions, in SOLID, WIREFRAME, MATERIAL or LIT shading (LIT: the scene's own lights and world).
``validate`` checks the request alone (the service does, before it queues anything); ``build`` turns it into
``angelo_view`` arguments with the scene's facts, gathered on the main thread (``external_visibility.look_facts``).
Every view is drawn from matrices, as the agent's are (``view_framing.free_camera``): no camera object is made and
nothing in the file changes. An app never names a picture source, a file, a region or a reference: a look is the
scene as it is now, never a path. RENDERED is not an app's: the agent's render look is a background render job that
adds a slot to Blender's Render Result, which Python cannot remove, so it would leave something in the file; an app
that needs the final render asks Angelo's agent (``angelo_send``), which renders under the person's approvals.
doc/angelo/external_mcp.rst, "Looks".
"""

import math

from . import view_spec

NAMED = view_spec.AXES + (view_spec.FIT, view_spec.SCENE_CAMERA)
PERSPECTIVE = {"orbit": [45.0, 30.0]}   # The default 3/4 view, through a lens (ISO is orthographic).
SHADINGS = ("SOLID", "WIREFRAME", "MATERIAL", view_spec.LIT)
NO_RENDER = ("RENDERED is not available to apps: a render look adds a slot to Blender's Render Result, which stays in "
             "the file. Use LIT (the scene's own lights and world) or ask Angelo's agent to render with angelo_send")
LENS_MM = view_spec.DEFAULT_LENS_MM
SENSOR_MM = 36.0   # view_framing's sensor: it spans the picture's longer side.
MARGIN = 1.15      # An orbit's bounding sphere fills at most this fraction less than the narrower side.
MAX_NAME = 255     # Blender's longest data-block name in bytes (MAX_ID_NAME 258, less its type code and NUL).
FIELDS = frozenset({"views", "objects", "resolution", "shading"})
VIEW_FIELDS = {"orbit": {"orbit", "distance"}, "camera": {"camera"}, "location": {"location", "look_at", "lens"}}
VIEW_WORDS = ("Each view is one of FRONT, BACK, LEFT, RIGHT, TOP, BOTTOM, ISO, perspective, FIT (the person's viewport "
              "direction), SCENE_CAMERA, {\"orbit\": [azimuth, elevation], \"distance\"?}, {\"camera\": name} or "
              "{\"location\": [x, y, z], \"look_at\": [x, y, z], \"lens\"?}")


def _number(value, low, high, what):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("%s must be a number from %g to %g" % (what, low, high))
    return float(value)


def _vector(value, what):
    if type(value) is not list or len(value) != 3:
        raise ValueError("%s is three numbers [x, y, z]" % what)
    return [_number(v, -view_spec.MAX_COORDINATE, view_spec.MAX_COORDINATE, what) for v in value]


def _name(value, what):
    if type(value) is not str or not value or len(value.encode("utf-8")) > MAX_NAME \
            or any(ord(c) < 32 for c in value):
        raise ValueError("%s is a name of 1-%d bytes of printable text" % (what, MAX_NAME))
    return value


def _view(value):
    if type(value) is str:
        word = value.strip().upper()
        if word == "PERSPECTIVE":
            return {"orbit": list(PERSPECTIVE["orbit"])}
        if word in NAMED:
            return word
        raise ValueError(VIEW_WORDS)
    if type(value) is not dict:
        raise ValueError(VIEW_WORDS)
    kind = next((key for key in VIEW_FIELDS if key in value), None)
    if kind is None or set(value) - VIEW_FIELDS[kind]:
        raise ValueError(VIEW_WORDS + "; nothing else (a reference, compare or picture is the agent's own)")
    if kind == "orbit":
        angles = value["orbit"]
        if type(angles) is not list or len(angles) != 2:
            raise ValueError("orbit is [azimuth, elevation] in degrees (azimuth 0 looks from the front, 90 from the "
                             "right; elevation 90 looks straight down)")
        item = {"orbit": [_number(angles[0], -360, 360, "orbit azimuth"),
                          _number(angles[1], -90, 90, "orbit elevation")]}
        if "distance" in value:
            item["distance"] = _number(value["distance"], 1e-4, view_spec.MAX_COORDINATE, "orbit distance")
        return item
    if kind == "camera":
        return {"camera": _name(value["camera"], "camera")}
    item = {"location": _vector(value["location"], "location"), "look_at": _vector(value.get("look_at"), "look_at")}
    if item["location"] == item["look_at"]:
        raise ValueError("A free camera's location and look_at are the same point")
    if "lens" in value:
        item["lens"] = _number(value["lens"], *view_spec.LENS_MM, "lens (mm)")
    return item


def validate(request):
    """The app's request checked and normalized, before anything is queued; ``ValueError`` says what is allowed."""
    if type(request) is not dict:
        raise ValueError("A look is an object: views, objects, resolution, shading")
    if set(request) - FIELDS:
        raise ValueError("A look takes views, objects, resolution and shading only (no image, file, path or region: a "
                         "look shows the scene as it is now)")
    views = request.get("views", [view_spec.FIT])
    if type(views) is not list or not 1 <= len(views) <= view_spec.MAX_VIEWS:
        raise ValueError("Request 1-%d views" % view_spec.MAX_VIEWS)
    views = [_view(view) for view in views]
    if len({repr(view) for view in views}) != len(views):
        raise ValueError("Repeated view")
    objects = request.get("objects", [])
    if type(objects) is not list or len(objects) > view_spec.MAX_TARGETS:
        raise ValueError("Name at most %d objects" % view_spec.MAX_TARGETS)
    objects = [_name(name, "Each object") for name in objects]
    if len(set(objects)) != len(objects):
        raise ValueError("Repeated object")
    resolution = request.get("resolution", 512)
    if type(resolution) is not int or resolution not in view_spec.RESOLUTIONS:
        raise ValueError("Choose a resolution of " + ", ".join(map(str, view_spec.RESOLUTIONS)) + " pixels")
    shading = request.get("shading", "SOLID")
    shading = shading.upper() if type(shading) is str else shading
    if shading == view_spec.RENDERED:
        raise ValueError(NO_RENDER)
    if shading not in SHADINGS:
        raise ValueError("Choose SOLID, WIREFRAME, MATERIAL or LIT shading")
    return {"views": views, "objects": objects, "resolution": resolution, "shading": shading}


def orbit_camera(item, bounds, aspect):
    """A free camera on the bounding sphere's orbit: azimuth 0 looks from the front (-Y, as FRONT), 90 from the right
    (+X); elevation 90 straight down. Without ``distance`` the whole sphere fits the lens's narrower side."""
    center, radius = bounds if bounds else ((0.0, 0.0, 0.0), 1.0)
    azimuth, elevation = (math.radians(a) for a in item["orbit"])
    direction = (math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation),
                 math.sin(elevation))
    half = math.atan(SENSOR_MM / 2.0 / max(aspect, 1.0 / max(aspect, 1e-6)) / LENS_MM)
    distance = item.get("distance") or max(radius, 1e-3) * MARGIN / math.sin(half)
    location = [round(c + distance * d, 9) for c, d in zip(center, direction)]
    return {"location": location, "look_at": [float(c) for c in center], "lens": LENS_MM}


def build(checked, facts):
    """``angelo_view`` arguments for a validated look. ``facts``: ``handles`` (each named object found, to its observed
    handle), ``bounds`` ((centre, radius) of the objects, else of the visible scene; None when empty), ``cameras``
    (each named scene camera found, to its free-camera item), ``aspect`` (the render's width over height) and
    ``known`` (some of the scene's object names, for a refusal)."""
    handles, cameras = facts.get("handles") or {}, facts.get("cameras") or {}
    missing = [name for name in checked["objects"] if name not in handles]
    known = list(facts.get("known") or sorted(handles))[:20]
    if missing:
        raise ValueError("No object named %s in the scene. Objects include: %s" % (
            ", ".join(repr(n) for n in missing), ", ".join(known) or "none"))
    views = []
    for view in checked["views"]:
        if type(view) is str or "location" in view:
            views.append(view)
        elif "orbit" in view:
            views.append(orbit_camera(view, facts.get("bounds"), float(facts.get("aspect") or 1.0)))
        elif view["camera"] in cameras:
            views.append(dict(cameras[view["camera"]]))
        else:
            raise ValueError("No camera named %r in the scene. Objects include: %s" % (
                view["camera"], ", ".join(sorted(set(cameras) | set(known))[:20]) or "none"))
    return {"views": views, "targets": [handles[name] for name in checked["objects"]],
            "resolution": checked["resolution"], "shading": checked["shading"]}
