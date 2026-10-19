# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed read-only visual observation contract. No Blender dependency.

``angelo_view`` returns actual native pixels of the bound scene. It never
changes scene data, selection, mode, frame or the artist's visible viewport.
Each entry of ``views`` chooses one camera: a named axis fitted to the targets,
FIT (the artist's current viewport direction fitted to the targets), the
scene's active camera, the exact camera of an earlier view_id (REUSE), or a
free camera given by location and look_at (CAMERA, ``view_framing.free_camera``);
a free camera with ``reference`` returns that attached image beside the view
(``view_compare``, agent_execution.rst "Free cameras and reference pictures"),
or with ``compare`` OVERLAY that image over the view, or OUTLINE the image's
subject and the targets' silhouette outlined on the view with their distances
(``view_outline``).

SOLID, WIREFRAME, MATCAP, CURVATURE, MATERIAL and LIT draw the viewport
(``view_looks``; LIT: EEVEE with the scene's own lights and world); DEPTH, like
WEIGHT, is drawn by the host from the targets alone (``view_depth``). RENDERED is the scene camera rendered by the
scene's own engine, small and bounded (``view_render``), or an existing
picture when ``image`` names one: a finished job's file or Blender's Render
Result (``view_finished``); ``file`` picks one output of a job that published
several (``view_job_output``). ``region`` asks for a part of that frame at
full detail instead of the whole frame scaled down.

Beyond the viewport (doc/angelo/view_sources.rst): ``image`` also names an
Image data-block's pixels (``IMAGE:<name>``), a mesh's UV layout (``UV``),
what the person sees (``EDITOR``) or one pass of a render
(``RENDER_RESULT:<pass>``, ``j<n>:<pass>``); ATTRIBUTE and WEIGHT shadings
show colour attributes and vertex weights; ``frames`` makes one strip of a
viewport look over several frames (``view_motion``).
"""

import json
import math
import re
import uuid

from .scene_spec import HANDLE, HANDLE_PATTERN

TOOL_NAME = "angelo_view"
AXES = ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM", "ISO")
FIT = "FIT"
SCENE_CAMERA = "SCENE_CAMERA"
FRAMINGS = AXES + (FIT, SCENE_CAMERA)
LIT, RENDERED, ATTRIBUTE, WEIGHT = "LIT", "RENDERED", "ATTRIBUTE", "WEIGHT"
MATCAP, CURVATURE, DEPTH = "MATCAP", "CURVATURE", "DEPTH"
SHADINGS = ("SOLID", "WIREFRAME", MATCAP, CURVATURE, "MATERIAL", ATTRIBUTE, WEIGHT, DEPTH, LIT, RENDERED)
RESOLUTIONS = (256, 384, 512, 768, 1024)
MAX_TARGETS = 16
MAX_VIEWS = 3
# A free camera (``CAMERA`` framing, view_framing.free_camera): a views item {location, look_at, lens |
# ortho_scale}, drawn from matrices like an axis view, so no camera object is made. With ``reference`` (an
# image attached to this conversation) its picture is that image beside the view (view_compare); a call of
# such pairs only may have MAX_PAIRS of them, each one picture of the request's budget (claude_evidence).
CAMERA = "CAMERA"
MAX_PAIRS = 6
MAX_COORDINATE = 1e6
LENS_MM = (1.0, 5000.0)          # action_camera_light_spec.CAMERA_FIELDS' bounds for a camera's lens and scale.
ORTHO_SCALE = (0.001, 100000.0)
DEFAULT_LENS_MM = 50.0           # Blender's new camera.
_CAMERA_FIELDS = frozenset({"location", "look_at", "lens", "ortho_scale", "reference", "compare"})
# How a reference is shown with its view (view_compare): beside it, over it, or as outlines with distances.
PAIR, OVERLAY, OUTLINE = "PAIR", "OVERLAY", "OUTLINE"
COMPARES = (PAIR, OVERLAY, OUTLINE)
# Bounds, three coordinates, field names and the reference's existence are checked by the host (_camera_item),
# whose one-line refusals cost a model less than a plan schema's (plan_schema.without_lengths).
_VECTOR = {"type": "array", "items": {"type": "number"}}
CAMERA_ITEM = {"type": "object", "properties": {
    "location": _VECTOR, "look_at": _VECTOR, "lens": {"type": "number"}, "ortho_scale": {"type": "number"},
    "reference": {"type": "string"}, "compare": {"type": "string", "enum": list(COMPARES)}},
    "required": ["location", "look_at"]}
# Read by every runtime: Claude's plan carries schemas without tool descriptions, and Codex shows a field's
# description where it shows no item's.
VIEWS_NOTE = ("1-3, or up to %d free cameras that all have reference. Free camera: world location and look_at, Z up, "
              "no roll (straight down: as TOP); lens mm on a 36 mm sensor (default %d) or ortho_scale; render "
              "aspect. reference: an attached image's reference_id: one picture, the image left of this view drawn "
              "at its aspect; compare OVERLAY: the image half over the view; OUTLINE (targets): both outlines and "
              "their distances." % (MAX_PAIRS, DEFAULT_LENS_MM))
# What a RENDERED look may cost, stated in the description below and enforced by view_render: the samples
# of one look, its wall-clock budget, and the rendering a single request may spend on looks in all.
RENDER_SAMPLES = 32
RENDER_SECONDS = 120
RENDER_REQUEST_SECONDS = 360
# ``image``: an existing picture instead of a render. A job ID (job_spec.JOB_ID, repeated so this contract
# imports no job module), this conversation's latest render, or Blender's Render Result.
# ``image`` may add ``:<name>`` to a source (view_sources.rst): the data-block of IMAGE, the texture of UV.
# EDITOR: the window as the person sees it, or with ``:<type>`` one editor.
RENDER_RESULT, LAST_JOB, IMAGE_DATA, UV, EDITOR = "RENDER_RESULT", "LAST_JOB", "IMAGE", "UV", "EDITOR"
JOB = r"^j[1-9][0-9]{0,8}$"
IMAGE = r"^(RENDER_RESULT|LAST_JOB|j[1-9][0-9]{0,8}|IMAGE|UV|EDITOR)(:.+)?$"
# Sources that show current state, made by view_finished's SOURCES like any picture, never a job to wait for.
CURRENT_SOURCES = (IMAGE_DATA, UV, EDITOR)
# Their pictures' framings, and a strip's: receipts count them as pictures of the scene now, not of a render.
CURRENT_FRAMINGS = ("IMAGE_DATA", "UV_LAYOUT", "EDITOR", "STRIP")
WAIT_SECONDS = 600  # Per request, shared by every pending render-job view.
# ``region``: [left, top, right, bottom], fractions of the frame from its top-left corner. Its shorter side
# is at least MIN_REGION of the frame, so a part is never a few pixels.
MIN_REGION = 1 / 64
MAX_FRAME = 1048574  # Blender's last frame.
# ``frames``: a strip of one viewport look over 2 to 8 frames (view_motion), never a render or WEIGHT.
MIN_FRAMES, MAX_FRAMES = 2, 8
STRIP_SHADINGS = ("SOLID", "WIREFRAME", "MATERIAL", ATTRIBUTE, LIT)
# ``file``: one output of a job, exactly as its receipt names it (job_store.output_file_name compares it with
# the reported names, so it is never a path of the model's making). validate_view enforces the length: a
# Claude plan's schema carries none.
MAX_FILE = 200

VIEW_TOOLS = [{
    "name": TOOL_NAME,
    "description": (
        "Read-only: return actual native images of the bound scene for visual judgment (silhouette, "
        "shading, toon look, lighting) that structured data cannot prove. Never edits data, selection, mode, "
        "frame or the artist's viewport. targets are observed object handles (empty = all visible "
        "geometry); every visible object can still appear and occlude, and geometry nearer than the "
        "fitted targets is cut away. Each views item is one camera: FRONT/BACK/LEFT/RIGHT/TOP/BOTTOM/ISO "
        "(orthographic, fitted to the targets with margin; FRONT looks along +Y), FIT (the artist's "
        "current viewport direction, fitted), SCENE_CAMERA (the scene's active camera and aspect), an "
        "earlier view_id to REUSE its exact camera for before/after comparison, or a free camera (views). "
        "SOLID, WIREFRAME, MATCAP (clay: form alone) and CURVATURE (cavity: ridges light, valleys dark) "
        "use fixed Workbench settings; MATERIAL is EEVEE Material Preview with the built-in HDRI, not scene "
        "lights; LIT is the same EEVEE draw with the scene's own lights and world instead (any view, about "
        "as fast: a quick approximation when the scene renders with Cycles); the first MATERIAL or LIT "
        "capture may pause Blender while shaders compile. RENDERED (views [\"SCENE_CAMERA\"] only) is the "
        "scene rendered as F12 renders it: its own engine, camera, lights, world, volumes, compositing and "
        "render-level modifiers, in the scene's view transform, at this resolution and at most %d samples "
        "(Cycles denoised). It returns when the render ends, which the person can cancel: seconds on a light "
        "scene, about %d s on a heavy one (stopped there once it has samples; a partial image says so), %d s "
        "of looks per request, never repeated after an edit. Use it to judge lighting, atmosphere and the "
        "final picture, and say what you saw instead of calling them unverified. Its result says how long it "
        "took and how much of that was preparing the scene: on a heavy scene iterate with LIT and render at "
        "checkpoints. region [left, top, right, "
        "bottom] (fractions of the frame from its top-left corner) renders only that part, at up to the "
        "scene's full resolution: use it for detail a downscaled frame loses. With image, RENDERED shows an "
        "existing picture, downscaled or its region at up to full size: a job ID "
        "or LAST_JOB (the latest render), waiting for render jobs still running (a RENDER_STILL's file, "
        "one frame of a RENDER_ANIMATION chosen with frame, or with file "
        "one of the job's outputs as it names them: a stereo view, a File Output item, a UDIM tile, a baked "
        "map) or RENDER_RESULT (Blender's current Render Result, such as the person's last render). Without "
        "file only the combined image is shown: no separate passes, layers, stereo views or video (one pass "
        "of a multilayer render: RENDER_RESULT:<pass>, j3:<pass> or LAST_JOB:<pass>, as Depth, Normal, "
        "CryptoObject00 or <layer>.<pass>; colour passes in the view transform, data passes as values the result "
        "states). OpenEXR "
        "is shown as an 8-bit preview in the scene's view transform (a non-colour map as its stored values, "
        "with none), and transparent pixels over a grey checkerboard; the result says so. "
        "image IMAGE:<name> shows an Image data-block's pixels as Blender holds them now (paint and bakes "
        "included; never loads a file: one not in memory is refused); UV (one mesh target) its active UV map "
        "over its active image texture, or over the image UV:<name>. ATTRIBUTE shows each mesh's active colour "
        "attribute; DEPTH draws only the targets, white nearest the camera to dark grey farthest, with those "
        "distances in its record; WEIGHT draws only the targets' vertex weights of group (default each one's "
        "active group; blue 0, green 0.5, red 1, unassigned black). image EDITOR shows the window as the person sees it now, "
        "EDITOR:<type> one editor (largest of that ui_type or type: UV, IMAGE_EDITOR, ShaderNodeTree, PROPERTIES "
        "...); text editors, consoles, Info, Preferences, file browser and status bar are greyed out. "
        "frames (2-8 frame numbers, one views item, SOLID, WIREFRAME, MATERIAL, ATTRIBUTE or LIT) returns one "
        "strip of that look at those frames, each tile labelled with its frame, the camera held over all of them "
        "(SCENE_CAMERA follows the scene camera); the scene is put back at its frame. A scene whose frame change "
        "runs add-on or script Python is refused (render those frames as an approved job). "
        "Results report each target's exact revision when supported, camera matrices, document epoch "
        "and explicit uncertainty. view_ids expire after load, Undo/Redo or restart. Request images at "
        "checkpoints, not after every edit." % (RENDER_SAMPLES, RENDER_SECONDS, RENDER_REQUEST_SECONDS)),
    "inputSchema": {
        "type": "object",
        "properties": {
            "targets": {"type": "array", "items": HANDLE, "maxItems": MAX_TARGETS,
                        "uniqueItems": True, "default": []},
            "views": {"type": "array", "minItems": 1, "maxItems": MAX_PAIRS, "uniqueItems": True,
                      "description": VIEWS_NOTE,
                      "items": {"anyOf": [{"type": "string", "enum": list(FRAMINGS)},
                                          {"type": "string", "format": "uuid"}, CAMERA_ITEM]}},
            "shading": {"type": "string", "enum": list(SHADINGS), "default": "SOLID"},
            "resolution": {"type": "integer", "enum": list(RESOLUTIONS), "default": 512},
            "image": {"type": "string", "pattern": IMAGE},
            "group": {"type": "string"},
            "file": {"type": "string", "maxLength": MAX_FILE},
            "region": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                       "minItems": 4, "maxItems": 4},
            "frame": {"type": "integer", "minimum": -MAX_FRAME, "maximum": MAX_FRAME},
            # Frame numbers and the least count are checked by the host (_check_frames): the schema stays compact.
            "frames": {"type": "array", "items": {"type": "integer"}, "maxItems": MAX_FRAMES},
        },
        "required": ["views"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}]
VIEW_NAMES = frozenset(tool["name"] for tool in VIEW_TOOLS)
_PROPERTIES = frozenset(VIEW_TOOLS[0]["inputSchema"]["properties"])


def framing_kind(view):
    """AXIS, FIT, SCENE_CAMERA, CAMERA (a free camera) or REUSE for one validated views item."""
    if type(view) is dict:
        return CAMERA
    if view in AXES:
        return "AXIS"
    if view in (FIT, SCENE_CAMERA):
        return view
    return "REUSE"


def _bounded(value, low, high):
    """Whether ``value`` is an int or a finite float from ``low`` to ``high``; an int of any size compares exactly
    (math.isfinite and float() would raise OverflowError on one past a float's range)."""
    return (type(value) is int or type(value) is float and math.isfinite(value)) and low <= value <= high


def _number(value, low, high, what):
    if not _bounded(value, low, high):
        raise ValueError("%s is a number from %g to %g" % (what, low, high))
    return float(value)


def _camera_item(value, index):
    """A validated free camera with its defaults filled (ValueError naming the field otherwise)."""
    where = "views[%d]" % index
    unknown = sorted(set(value) - _CAMERA_FIELDS, key=str)
    if unknown:
        raise ValueError("%s has unknown field %s: a free camera takes location, look_at, lens or ortho_scale, and "
                         "reference" % (where, str(unknown[0])[:40]))
    item = {}
    for field in ("location", "look_at"):
        point = value.get(field)
        if type(point) is not list or len(point) != 3 or not all(
                _bounded(v, -MAX_COORDINATE, MAX_COORDINATE) for v in point):
            raise ValueError("%s.%s is [x, y, z]: three finite world coordinates of at most %g"
                             % (where, field, MAX_COORDINATE))
        item[field] = [float(v) + 0.0 for v in point]   # -0.0 is 0.0: the same camera, a repeated view.
    if math.dist(item["location"], item["look_at"]) < 1e-4:
        raise ValueError("%s.look_at must be at least 0.0001 from location: the camera looks from location toward it"
                         % where)
    if "lens" in value and "ortho_scale" in value:
        raise ValueError("%s has lens and ortho_scale: give lens (perspective, mm) or ortho_scale (orthographic), "
                         "not both" % where)
    if "ortho_scale" in value:
        item["ortho_scale"] = _number(value["ortho_scale"], *ORTHO_SCALE, where + ".ortho_scale (world units)")
    else:
        item["lens"] = _number(value.get("lens", DEFAULT_LENS_MM), *LENS_MM, where + ".lens (mm)")
    if "reference" in value:
        try:
            item["reference"] = str(uuid.UUID(value["reference"]))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("%s.reference is the reference_id of an image attached to this conversation"
                             % where) from None
    if "compare" in value:
        if type(value["compare"]) is not str or value["compare"] not in COMPARES:
            raise ValueError("%s.compare is PAIR (beside), OVERLAY (over) or OUTLINE (outlines and distances)" % where)
        if "reference" not in item:
            raise ValueError("%s.compare shows a reference with this view: give reference too" % where)
        if value["compare"] != PAIR:
            item["compare"] = value["compare"]
    return item


def _view_item(value, index):
    if type(value) is dict:
        return _camera_item(value, index)
    if type(value) is not str:
        raise ValueError("Each view is an axis name, FIT, SCENE_CAMERA, an earlier view_id or a free camera "
                         "object")
    if value in FRAMINGS:
        return value
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ValueError("Each view is an axis name, FIT, SCENE_CAMERA, an earlier view_id or a free camera "
                         "object") from None


def _identity(view):
    """A hashable identity of a validated views item, so repeated items are found."""
    return json.dumps(view, sort_keys=True) if type(view) is dict else view


def check_region(value):
    """A validated region as four floats (ValueError otherwise)."""
    if type(value) is not list or len(value) != 4 or not all(_bounded(item, -1e300, 1e300) for item in value):
        raise ValueError("region is [left, top, right, bottom]: four fractions of the frame from its top-left corner")
    left, top, right, bottom = (float(item) for item in value)
    if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
        raise ValueError("region needs 0 <= left < right <= 1 and 0 <= top < bottom <= 1 (fractions of the frame "
                         "from its top-left corner)")
    if min(right - left, bottom - top) < MIN_REGION:
        raise ValueError("region is too thin: each side is at least 1/64 of the frame")
    return [left, top, right, bottom]


def split_image(value):
    """``(source, name)`` of a validated ``image``: ``name`` is the part after the first colon, or None."""
    source, colon, name = value.partition(":")
    return source, (name if colon else None)


def is_job_source(source):
    """Whether an ``image`` source names a job of this conversation (its ID or LAST_JOB)."""
    return source == LAST_JOB or re.fullmatch(JOB, source) is not None


def _printable(value, what):
    if type(value) is not str or not value or len(value) > MAX_FILE or any(ord(c) < 32 for c in value):
        raise ValueError("%s is at most %d printable characters (this one has %d)"
                         % (what, MAX_FILE, len(value) if type(value) is str else 0))
    return value


def _check_image(value, checked):
    """Validate ``image`` against the other checked arguments; returns it unchanged (validation is idempotent)."""
    if type(value) is not str or re.fullmatch(IMAGE, value) is None:
        raise ValueError("image is RENDER_RESULT, LAST_JOB or the ID of a render job such as j3, or IMAGE:<image "
                         "name>, UV or EDITOR")
    if checked["shading"] != RENDERED:
        raise ValueError("image shows an existing picture: use it with RENDERED shading and views [\"SCENE_CAMERA\"]")
    source, name = split_image(value)
    if name is not None:
        _printable(name, "The name after the colon in image")
    if source == IMAGE_DATA and name is None:
        raise ValueError("IMAGE:<name> names one Image data-block, such as IMAGE:Albedo")
    if source == UV and len(checked["targets"]) != 1:
        raise ValueError("UV shows one mesh's UV layout: pass that mesh's handle as the only target")
    return value  # After the colon: the data-block, texture, editor type, or a render's [layer.]pass.


def _check_frames(frames, checked):
    if (type(frames) is not list or not MIN_FRAMES <= len(frames) <= MAX_FRAMES
            or any(type(frame) is not int or not -MAX_FRAME <= frame <= MAX_FRAME for frame in frames)):
        raise ValueError("frames is a list of 2 to 8 frame numbers")
    if len(set(frames)) != len(frames):
        raise ValueError("Repeated frame in frames")
    if checked["shading"] not in STRIP_SHADINGS:
        raise ValueError("frames draws one viewport look over frames: use SOLID, WIREFRAME, MATERIAL, ATTRIBUTE or LIT "
                         "(a RENDER_ANIMATION job's frames are shown with image and frame)")
    if len(checked["views"]) != 1:
        raise ValueError("frames makes one strip from one views item")
    return list(frames)


def validate_view(name, args):
    """Validate standalone and return normalized arguments with defaults filled."""
    if name not in VIEW_NAMES or type(args) is not dict:
        raise ValueError("Unknown view tool or non-object arguments")
    if set(args) - _PROPERTIES or "views" not in args:
        raise ValueError("Unexpected or missing view argument")
    targets = args.get("targets", [])
    if type(targets) is not list or len(targets) > MAX_TARGETS:
        raise ValueError(f"Choose at most {MAX_TARGETS} observed object handles")
    for handle in targets:
        if type(handle) is not str or re.fullmatch(HANDLE_PATTERN, handle) is None:
            raise ValueError("Targets must be exact observed scene object handles; search the scene first")
    if len(set(targets)) != len(targets):
        raise ValueError("Repeated view target")
    views = args["views"]
    if type(views) is not list or not 1 <= len(views) <= MAX_PAIRS:
        raise ValueError(f"Request 1-{MAX_VIEWS} views per call, or 1-{MAX_PAIRS} free cameras that all have reference")
    views = [_view_item(view, index) for index, view in enumerate(views)]
    if len(views) > MAX_VIEWS and not all(type(view) is dict and "reference" in view for view in views):
        raise ValueError(f"Request 1-{MAX_VIEWS} views per call; only free cameras that all have reference may be "
                         f"up to {MAX_PAIRS} (this call has {len(views)} views)")
    if len({_identity(view) for view in views}) != len(views):
        raise ValueError("Repeated view")
    shading = args.get("shading", "SOLID")
    if type(shading) is not str or shading not in SHADINGS:
        raise ValueError("Choose SOLID, WIREFRAME, MATCAP, CURVATURE, MATERIAL, ATTRIBUTE, WEIGHT, DEPTH, LIT or "
                         "RENDERED shading")
    resolution = args.get("resolution", 512)
    if type(resolution) is not int or resolution not in RESOLUTIONS:
        raise ValueError("Choose a view resolution of 256, 384, 512, 768 or 1024 pixels")
    checked = {"targets": list(targets), "views": views, "shading": shading, "resolution": resolution}
    if shading == RENDERED and views != [SCENE_CAMERA]:
        raise ValueError('RENDERED renders the scene\'s own camera: pass views ["SCENE_CAMERA"] alone, '
                         "or choose LIT (the scene's lights and world, any view), SOLID, WIREFRAME or MATERIAL")
    if "image" in args:
        checked["image"] = _check_image(args["image"], checked)
    source = split_image(checked["image"])[0] if "image" in checked else ""
    if shading == WEIGHT and not targets:
        raise ValueError("WEIGHT draws the vertex weights of mesh targets: pass their observed handles")
    if shading == DEPTH and not targets:
        raise ValueError("DEPTH draws only the targets' distance from the camera: pass their observed handles")
    if not targets and any(type(view) is dict and view.get("compare") == OUTLINE for view in views):
        raise ValueError("compare OUTLINE outlines the targets: pass their observed handles")
    if "group" in args:
        if shading != WEIGHT:
            raise ValueError("group names the vertex group WEIGHT shading draws")
        checked["group"] = _printable(args["group"], "group (a vertex group's name)")
    if "frames" in args:
        checked["frames"] = _check_frames(args["frames"], checked)
        if any(type(view) is dict and "reference" in view for view in views):
            raise ValueError("views[0].reference pairs one view with an image; frames makes one strip of several "
                             "frames: use reference or frames, not both")
    if "file" in args:
        name = args["file"]
        if type(name) is not str or not name or len(name) > MAX_FILE or any(ord(c) < 32 for c in name):
            raise ValueError("file is one output of a finished job as its outputs name it: at most %d printable "
                             "characters (this one has %d)" % (MAX_FILE, len(name) if type(name) is str else 0))
        if not is_job_source(source):
            raise ValueError("file names one output of a job: pass that job's ID or LAST_JOB as image")
        checked["file"] = name
    if "region" in args:
        if shading != RENDERED:
            raise ValueError("region is a part of the rendered frame: use it with RENDERED shading (a viewport view "
                             "is framed by its targets instead)")
        checked["region"] = check_region(args["region"])
    if "frame" in args:
        frame = args["frame"]
        if type(frame) is not int or not -MAX_FRAME <= frame <= MAX_FRAME:
            raise ValueError("frame is the number of one frame of a finished animation job")
        if not is_job_source(source):
            raise ValueError("frame chooses one frame of a RENDER_ANIMATION job: pass that job's ID or LAST_JOB as "
                             "image (a look renders the current frame)")
        checked["frame"] = frame
    return checked


# What a normalised request did, for the result the model reads (plan_validation).
RENDERED_SPLIT = ("RENDERED renders only the scene camera, so the host rendered SCENE_CAMERA and drew %s with LIT "
                  "(the scene's lights and world, any view): results lists both")
PICTURE_SHOWN = ("image shows an existing picture, which no camera or shading changes, so the host showed it as "
                 "RENDERED with views [\"SCENE_CAMERA\"]")


def _valid(calls):
    try:
        for call in calls:
            validate_view(TOOL_NAME, call)
    except ValueError:
        return False
    return True


def normalize_view(args):
    """The calls an invalid view request means unambiguously, with a note; None when it can mean more than one thing.

    Two classes (doc/angelo/agent_execution.rst, "Visual observation"), each
    validated whole afterwards, so the request's only fault was the one fixed:

    * RENDERED with ``SCENE_CAMERA`` and named cameras (axes, FIT): RENDERED
      renders only the scene camera, and another camera's look with the
      scene's lights and world is LIT, so it becomes RENDERED for
      ``SCENE_CAMERA`` plus LIT for the others (no other argument than targets
      and resolution);
    * an existing picture (``image``) with one views item and shading left
      out or RENDERED: no camera or shading changes a picture, so it is shown
      as RENDERED ``SCENE_CAMERA``, the only form the contract has for it.

    In the recorded 1.0 runs the model's own correction was exactly this in 13
    of 14 and 2 of 2 refusals (the 14th asked for the render alone). Not
    normalised: an earlier view_id with RENDERED, beside ``SCENE_CAMERA`` or
    not (it may mean the scene camera's render or a LIT look of that exact
    camera, which may have moved since), ``frame`` without a job picture (a scene frame no
    read-only look can show; dropping it shows another frame), an explicit
    viewport shading beside ``image``, and anything else.
    """
    if type(args) is not dict or type(args.get("views")) is not list:
        return None
    views = args["views"]
    if any(type(view) is not str for view in views) or len(set(views)) != len(views):
        return None
    if "image" in args:
        call = dict(args, shading=RENDERED, views=[SCENE_CAMERA])
        if args.get("shading", RENDERED) != RENDERED or len(views) != 1 or call == args or not _valid([call]):
            return None
        return [call], PICTURE_SHOWN
    if (args.get("shading") != RENDERED or set(args) - {"targets", "views", "shading", "resolution"}
            or not 1 < len(views) <= MAX_VIEWS or SCENE_CAMERA not in views
            or any(view not in FRAMINGS for view in views)):
        return None
    others = [view for view in views if view != SCENE_CAMERA]
    calls = [dict(args, views=[SCENE_CAMERA]), dict(args, views=others, shading=LIT)]
    return (calls, RENDERED_SPLIT % ", ".join(others)) if _valid(calls) else None
