# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background jobs for long work: the typed request and its bounds. No Blender dependency.

Renders, bakes and large exports run as Blender's own window-manager jobs, never
inside a staged program, a commit or a reviewed script (doc/angelo/long_jobs.rst).
``angelo_job_start`` names what to run, where its output goes and its bounds;
the host answers at once with a job ID. Progress and the result reach the agent
in the next message's jobs block (``job_store.context_entries``), never by polling.

Bounds are checked here before anything starts. A request beyond them is refused
with the bound and its limit, so the agent can ask the person instead of guessing.
"""

import math
import re

from .action_world_spec import IMAGE_ID_PATTERN
from .refusal_codes import RefusalError
from .scene_spec import HANDLE

KINDS = ("RENDER_STILL", "RENDER_ANIMATION", "BAKE_TEXTURE", "BAKE_PHYSICS", "BAKE_SIMULATION",
         "EXPORT_USD", "EXPORT_ALEMBIC")
RENDERS = frozenset({"RENDER_STILL", "RENDER_ANIMATION"})
# Not a kind of angelo_job_start: the bounded render behind angelo_view's RENDERED shading (view_render).
# It runs through the same manager (one job at a time, progress, Cancel, time budget), is answered by its
# own tool call, and leaves no record, notice or jobs-block entry behind.
PREVIEW = "RENDER_PREVIEW"
BAKES = frozenset({"BAKE_TEXTURE", "BAKE_PHYSICS", "BAKE_SIMULATION"})
EXPORTS = frozenset({"EXPORT_USD", "EXPORT_ALEMBIC"})
# Blender locks its own interface while these run (wm_jobs: WM_locked_interface_set), and the
# job thread drives the viewport depsgraph and frame: nothing may read or write scene data.
LOCKING = frozenset({"BAKE_PHYSICS", "BAKE_SIMULATION", "EXPORT_USD", "EXPORT_ALEMBIC"})
# The image formats a render job writes, and their file suffix. SCENE keeps the scene's own image settings, any
# format Blender writes as F12 would (multilayer OpenEXR, TIFF, DPX..., with its codec, colour depth and mode; the
# agent sets them with property_set render.image_settings.*); its suffix comes from that format at admission. One
# enum item, not one per format: Claude's and Codex's instructions carry this schema on every turn.
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "OPEN_EXR": ".exr", "SCENE": None}
SCENE_FORMAT = "SCENE"
# A frame-name pattern: a stem, then the frame number's '#' run (its padding), at the end (Blender's own rule).
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,90}#{1,8}$"
EXPORT_SUFFIXES = {"EXPORT_USD": (".usd", ".usdc", ".usda"), "EXPORT_ALEMBIC": (".abc",)}
BAKE_TYPES = ("COMBINED", "AO", "SHADOW", "POSITION", "NORMAL", "UV", "ROUGHNESS", "EMIT",
              "ENVIRONMENT", "DIFFUSE", "GLOSSY", "TRANSMISSION")
# The maps that hold data, not colours: a new bake image for one is Non-Color, and a view of one shows its
# stored values without a view transform (view_job_output).
DATA_MAPS = frozenset({"NORMAL", "POSITION", "UV", "ROUGHNESS", "AO", "SHADOW"})
# Which optional fields each kind takes; any other field is refused, never ignored.
FIELDS = {
    "RENDER_STILL": {"output", "frame_start", "resolution", "samples", "format"},
    # An animation renders on the scene's own frame step, as F12 does (property_set frame_step sets it).
    "RENDER_ANIMATION": {"output", "frame_start", "frame_end", "name", "resolution", "samples", "format"},
    "BAKE_TEXTURE": {"target", "bake_type", "samples", "output", "sources", "cage", "extrusion", "ray_distance",
                     "image", "resolution"},
    "BAKE_PHYSICS": set(),
    "BAKE_SIMULATION": {"target", "output"},
    "EXPORT_USD": {"output", "frame_start", "frame_end"},
    "EXPORT_ALEMBIC": {"output", "frame_start", "frame_end"},
}
# A simulation bake names its object: Blender bakes the selected objects, never the agent's guess.
REQUIRED = {"RENDER_STILL": {"output"}, "RENDER_ANIMATION": {"output"}, "BAKE_TEXTURE": {"target"},
            "BAKE_SIMULATION": {"target"}, "EXPORT_USD": {"output"}, "EXPORT_ALEMBIC": {"output"}}
DEFAULT_MINUTES = {"RENDER_STILL": 30, "RENDER_ANIMATION": 240, "BAKE_TEXTURE": 60, "BAKE_PHYSICS": 120,
                   "BAKE_SIMULATION": 120, "EXPORT_USD": 60, "EXPORT_ALEMBIC": 60}
MAX_MINUTES = 720
MAX_FRAMES = 10000
MAX_SIDE = 16384
MAX_PIXELS = 100_000_000
MAX_SAMPLES = 16384
# Pixels x samples x frames: about 600 frames of 4K at 4,096 samples.
MAX_WORK = 2 * 10 ** 13
MAX_QUEUED = 4
# A high-to-low bake runs once per source object; rays start at most this far from the target (scene units).
MAX_SOURCES = 8
MAX_OUTPUT = 1024  # Characters of an output path. validate_job enforces it: a Claude plan's schema carries no lengths.
MAX_RAY = 1000.0
JOB_ID = re.compile(r"^j[1-9][0-9]{0,8}$")
_FRAME = {"type": "integer", "minimum": -1048574, "maximum": 1048574}
# Fields every kind takes besides its own (FIELDS): validate_job reads them for all kinds.
SHARED_FIELDS = ("minutes", "queue")
# The schema's property order, which the kinds' field lists follow.
_ORDER = ("output", "frame_start", "frame_end", "name", "resolution", "samples", "format", "target", "bake_type",
          "sources", "cage", "extrusion", "ray_distance", "image")


def fields_by_kind():
    """The declaration's statement of which fields each kind takes, made from FIELDS and REQUIRED.

    The schema is one flat object for every kind, so a field there is offered to all of them while validate_job
    refuses it for the kinds that don't take it: on 2026-10-29 a GPT-6.1 Sol plan named a RENDER_STILL (name is an
    animation's), and the correction round put the whole job schema in the chat. Stating each kind's fields from the
    table the validator reads means the declaration never offers a field the host refuses for that kind. Kinds with
    the same fields share an entry; '*' marks a required field.
    """
    entries, seen = [], {}
    for kind in KINDS:
        listed = " ".join(field + ("*" if field in REQUIRED.get(kind, ()) else "")
                          for field in _ORDER if field in FIELDS[kind]) or "no other field"
        if listed in seen:
            entries[seen[listed]][0].append(kind)
        else:
            seen[listed] = len(entries)
            entries.append(([kind], listed))
    return ("Fields by kind (* required; any other is refused): "
            + "; ".join("/".join(kinds) + ": " + listed for kinds, listed in entries)
            + ". Every kind takes " + " and ".join(SHARED_FIELDS) + ".")


JOB_TOOLS = [
    {
        "name": "angelo_job_start",
        "description": (
            "Start a render, bake or USD/Alembic export as a background Blender job after the person approves "
            "it. Returns a job ID immediately. Later messages report progress/results in a jobs block. Never "
            "sleep or poll; angelo_view (image LAST_JOB) waits for a render picture. One job runs at a time; "
            "queue=true queues behind it. Scene changes are refused while a job runs. output is a workspace-relative path "
            "(a file, or a folder for an animation, several views or baked images). A job never overwrites: a taken "
            "name gets the next free one (<name>-<job>), which the result names. "
            "resolution, samples and frames apply to this job only. format SCENE keeps the scene's own image "
            "settings; name is the frame pattern (sh010.####). BAKE_TEXTURE with sources bakes those objects "
            "onto target; image is the image to bake into, or resolution makes a new one. " + fields_by_kind()),
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "output": {"type": "string", "minLength": 1, "maxLength": MAX_OUTPUT},
            "frame_start": dict(_FRAME),
            "frame_end": dict(_FRAME),
            "name": {"type": "string", "pattern": NAME_PATTERN, "maxLength": 100},
            "resolution": {"type": "array", "items": {"type": "integer", "minimum": 4, "maximum": MAX_SIDE},
                           "minItems": 2, "maxItems": 2},
            "samples": {"type": "integer", "minimum": 1, "maximum": MAX_SAMPLES},
            "format": {"type": "string", "enum": list(FORMATS)},
            "target": dict(HANDLE),
            "bake_type": {"type": "string", "enum": list(BAKE_TYPES)},
            "sources": {"type": "array", "items": dict(HANDLE), "minItems": 1, "maxItems": MAX_SOURCES},
            "cage": dict(HANDLE),
            "extrusion": {"type": "number", "minimum": 0, "maximum": MAX_RAY},
            "ray_distance": {"type": "number", "minimum": 0, "maximum": MAX_RAY},
            "image": {"type": "string", "pattern": IMAGE_ID_PATTERN, "maxLength": 80},
            "minutes": {"type": "number", "minimum": 0.1, "maximum": MAX_MINUTES},
            "queue": {"type": "boolean"},
        }, "required": ["kind"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    },
    {
        "name": "angelo_job_cancel",
        "description": ("Cancel a queued or running job this conversation started. Needs no approval: it only stops "
                        "approved work and restores what the job changed (completed animation frames are kept)."),
        "inputSchema": {"type": "object", "properties": {"job": {"type": "string", "pattern": JOB_ID.pattern}},
                        "required": ["job"], "additionalProperties": False},
        # Changes no scene data or files beyond undoing the job's own temporary changes.
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
]
JOB_NAMES = frozenset(tool["name"] for tool in JOB_TOOLS)


def native_jobs_available():
    """Whether this process may offer the job tools: inside Angelo only when its binary has ``_bpy._angelo_jobs``.

    Release builds always have it. A stale development binary without it would refuse
    every job at start, so the tools are not declared there at all. Outside Blender
    (host tests, measurements) the normal build's surface is assumed.
    """
    try:
        import _bpy
    except ImportError:
        return True
    return hasattr(_bpy, "_angelo_jobs")


class JobRequestError(RefusalError):
    """A job request failure; only explicit codes identify known preconditions."""
    partial_changes_possible = False


def _integer(value, low, high, what):
    if type(value) is not int or not low <= value <= high:
        raise JobRequestError(f"{what} must be a whole number from {low:,} to {high:,}", code="VALIDATION")
    return value


def _handle(value, what):
    if not isinstance(value, str) or not re.fullmatch(HANDLE["pattern"], value):
        raise JobRequestError(f"{what} is an observed object handle", code="VALIDATION")
    return value


def _distance(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 <= value <= MAX_RAY:
        raise JobRequestError(f"{what} is a distance from 0 to {MAX_RAY:g} scene units", code="VALIDATION")
    return float(value)


def _bake_sources(args, request):
    """The high-to-low fields of a texture bake: source objects, an optional cage and where rays start and end."""
    if "sources" in args:
        sources = args["sources"]
        if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
            raise JobRequestError("sources lists 1 to %d observed object handles to bake from (this one has %d)"
                                  % (MAX_SOURCES, len(sources) if type(sources) is list else 0), code="VALIDATION")
        request["sources"] = [_handle(item, "Each of sources") for item in sources]
        if len(set(request["sources"])) != len(sources) or request["target"] in request["sources"]:
            raise JobRequestError("sources are other objects than target, each named once", code="VALIDATION")
    for field in ("cage", "extrusion", "ray_distance"):
        if field in args and "sources" not in args:
            raise JobRequestError(f"{field} applies to a bake from sources; name the objects to bake from", code="VALIDATION")
    if "cage" in args:
        request["cage"] = _handle(args["cage"], "cage")
        if request["cage"] == request["target"] or request["cage"] in request["sources"]:
            raise JobRequestError("cage is its own object: a copy of target that encloses the sources", code="VALIDATION")
    for field in ("extrusion", "ray_distance"):
        if field in args:
            request[field] = _distance(args[field], field)
    if "image" in args:
        if not isinstance(args["image"], str) or not re.fullmatch(IMAGE_ID_PATTERN, args["image"]):
            raise JobRequestError("image is the resource ID of a loaded image (dat:...:IMAGE:...)", code="VALIDATION")
        request["image"] = args["image"]
        if "resolution" in args:
            raise JobRequestError("Pass image to bake into a loaded image or resolution for a new one, not both", code="VALIDATION")


_WINDOWS_DEVICES = frozenset({"CON", "PRN", "AUX", "NUL", *("COM%d" % n for n in range(1, 10)),
                              *("LPT%d" % n for n in range(1, 10))})


# The extensions Blender recognizes on an output name and replaces (imbuf util.cc imb_ext_image): a frame number
# that completes one ("x.jp#" at frame 2 is "x.jp2") would make Blender write another file than the plan names.
IMAGE_EXTENSIONS = (".png", ".tga", ".bmp", ".jpg", ".jpeg", ".sgi", ".rgb", ".rgba", ".tif", ".tiff", ".tx", ".avif",
                    ".jp2", ".j2c", ".hdr", ".dds", ".dpx", ".cin", ".exr", ".psd", ".pdd", ".psb", ".webp")


def name_problem(name):
    """Why a frame pattern (a stem and a '#' run) can't name a job's files, or None: a Windows device name for any
    frame number (CON, COM1..., before the first dot), or a frame number completing an image extension."""
    stem = name.rstrip("#")
    base = stem.split(".", 1)[0].upper()
    if base in _WINDOWS_DEVICES or ("." not in stem and stem.upper() in ("COM", "LPT")):
        return "is a device name on Windows"
    if "." in stem:
        tail = stem.rsplit(".", 1)[1].lower()
        if any(re.fullmatch(re.escape(tail) + r"[0-9]+", extension[1:]) for extension in IMAGE_EXTENSIONS):
            return "turns into an image file extension with a frame number"
    return None


def frame_count(start, end, step=1):
    return (end - start) // max(1, step) + 1


def still_frame(args):
    """A RENDER_STILL's frame as models write it: ``frame`` alone is ``frame_start``, and a ``frame_end`` equal to
    ``frame_start`` names the same one frame, so it is dropped. A still's start was refused twice for these on the
    bridge one-shot (``performance.rst``, "Castle one-shot misses"). Only whole frames in range are read so; anything
    else, and every other kind, is left exactly as written, so its refusal is unchanged."""
    if not isinstance(args, dict) or args.get("kind") != "RENDER_STILL" or not {"frame", "frame_end"} & set(args):
        return args
    read = dict(args)
    if "frame" in read and "frame_start" not in read and _whole_frame(read["frame"]):
        read["frame_start"] = read.pop("frame")
    if "frame_end" in read and _whole_frame(read.get("frame_start")) and read["frame_end"] == read["frame_start"] \
            and type(read["frame_end"]) is int:
        del read["frame_end"]
    return args if {"frame", "frame_end"} & set(read) else read


def _whole_frame(value):
    return type(value) is int and _FRAME["minimum"] <= value <= _FRAME["maximum"]


def validate_job(name, args):
    """The checked, normalized arguments of one job tool call (raises JobRequestError)."""
    args = still_frame(args)
    if name == "angelo_job_cancel":
        if set(args) != {"job"} or not isinstance(args["job"], str) or not JOB_ID.fullmatch(args["job"]):
            raise JobRequestError("Name the job to cancel by its ID, such as j3", code="VALIDATION")
        return {"job": args["job"]}
    if name != "angelo_job_start":
        raise JobRequestError("Unknown job tool", code="VALIDATION")
    kind = args.get("kind")
    if kind not in KINDS:
        raise JobRequestError("kind must be one of: " + ", ".join(KINDS), code="VALIDATION")
    extra = set(args) - FIELDS[kind] - {"kind", "minutes", "queue"}
    if extra:
        # Where those fields belong, as a fact: a request for the wrong kind is corrected in one round, not two.
        takers = [other for other in KINDS if other != kind and extra <= FIELDS[other]]
        raise JobRequestError(f"{kind} does not take: " + ", ".join(sorted(extra))
                              + ("; kinds that take them: " + ", ".join(takers) if takers else ""), code="VALIDATION")
    missing = REQUIRED.get(kind, set()) - set(args)
    if missing:
        raise JobRequestError(f"{kind} needs: " + ", ".join(sorted(missing)), code="VALIDATION")
    request = {"kind": kind, "queue": args.get("queue", False) is True}
    if "queue" in args and type(args["queue"]) is not bool:
        raise JobRequestError("queue must be true or false", code="VALIDATION")
    minutes = args.get("minutes", DEFAULT_MINUTES[kind])
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) \
            or not 0.1 <= minutes <= MAX_MINUTES:
        raise JobRequestError(f"minutes (the time budget) must be from 0.1 to {MAX_MINUTES}", code="VALIDATION")
    request["minutes"] = float(minutes)
    if "output" in args:
        output = args["output"]
        if not isinstance(output, str) or not output.strip() or len(output) > MAX_OUTPUT or any(ord(c) < 32 for c in output):
            raise JobRequestError("output must be a printable workspace-relative path of at most %d characters (this "
                                  "one has %d)" % (MAX_OUTPUT, len(output) if isinstance(output, str) else 0), code="VALIDATION")
        request["output"] = output.strip().rstrip("/")
    for field in ("frame_start", "frame_end"):
        if field in args:
            request[field] = _integer(args[field], -1048574, 1048574, field)
    if "name" in args:
        if not isinstance(args["name"], str) or not re.fullmatch(NAME_PATTERN, args["name"]):
            raise JobRequestError("name is a frame-name pattern: letters, digits, '.', '_' or '-', then 1 to 8 '#' "
                                  "for the frame number at the end, such as sh010_v003.####", code="VALIDATION")
        problem = name_problem(args["name"])
        if problem:
            raise JobRequestError("name %s %s; choose another" % (args["name"], problem), code="VALIDATION")
        request["name"] = args["name"]
    if "frame_start" in request and "frame_end" in request:
        if request["frame_end"] < request["frame_start"]:
            raise JobRequestError("frame_end must not be before frame_start", code="VALIDATION")
        if frame_count(request["frame_start"], request["frame_end"], request.get("frame_step", 1)) > MAX_FRAMES:
            raise JobRequestError(f"At most {MAX_FRAMES:,} frames per job; ask the person whether to split the range", code="VALIDATION")
    if "resolution" in args:
        value = args["resolution"]
        if type(value) is not list or len(value) != 2:
            raise JobRequestError("resolution is [width, height] in pixels", code="VALIDATION")
        width, height = (_integer(side, 4, MAX_SIDE, "Each resolution side") for side in value)
        if width * height > MAX_PIXELS:
            raise JobRequestError(f"At most {MAX_PIXELS:,} pixels per frame (asked {width * height:,})", code="VALIDATION")
        request["resolution"] = [width, height]
    if "samples" in args:
        request["samples"] = _integer(args["samples"], 1, MAX_SAMPLES, "samples")
    if "format" in args:
        if args["format"] not in FORMATS:
            raise JobRequestError("format must be one of: " + ", ".join(FORMATS), code="VALIDATION")
        request["format"] = args["format"]
    if "bake_type" in args:
        if args["bake_type"] not in BAKE_TYPES:
            raise JobRequestError("bake_type must be one of: " + ", ".join(BAKE_TYPES), code="VALIDATION")
        request["bake_type"] = args["bake_type"]
    if "target" in args:
        request["target"] = _handle(args["target"], "target")
    if kind == "BAKE_TEXTURE":
        _bake_sources(args, request)
    if kind in RENDERS:
        request.setdefault("format", "PNG")
        suffix = FORMATS[request["format"]]
        if suffix is None:   # The scene's own format: its suffix is checked at admission (job_render_scene.Plan).
            pass
        elif kind == "RENDER_STILL" and not request["output"].lower().endswith(suffix):
            if "." in request["output"].rsplit("/", 1)[-1]:
                raise JobRequestError(f"A {request['format']} still is written to a {suffix} file", code="VALIDATION")
            request["output"] += suffix
    if kind in EXPORTS and not request["output"].lower().endswith(EXPORT_SUFFIXES[kind]):
        raise JobRequestError(f"{kind} writes a " + " or ".join(EXPORT_SUFFIXES[kind]) + " file", code="VALIDATION")
    if kind == "BAKE_TEXTURE":
        request.setdefault("bake_type", "COMBINED")
    return request


def check_frames(start, end, source="the request", step=1):
    """The frames a job will really use, wherever they come from (``source``: "the scene", "the cache")."""
    if end < start:
        raise JobRequestError(f"The frame range of {source} ends before it starts ({start} to {end})", code="VALIDATION")
    count = frame_count(start, end, step)
    if count > MAX_FRAMES:
        raise JobRequestError(
            f"The frame range of {source}, {start} to {end}, is {count:,} frames; a job does at most "
            f"{MAX_FRAMES:,}. Ask the person for a shorter range (frame_start and frame_end)", code="LIMIT_EXCEEDED")
    return count


def check_resolution(width, height, source="the request"):
    """The pixels a job will really render or bake into, wherever they come from."""
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE) or width * height > MAX_PIXELS:
        raise JobRequestError(
            f"The resolution of {source} is {width}x{height}; a job allows at most {MAX_SIDE:,} pixels a side and "
            f"{MAX_PIXELS:,} in all. Ask the person whether to use a smaller resolution", code="LIMIT_EXCEEDED")


def check_samples(samples, source="the request"):
    if samples > MAX_SAMPLES:
        raise JobRequestError(
            f"The sample count of {source} is {samples:,}; a job allows at most {MAX_SAMPLES:,}. "
            "Ask the person whether to pass fewer samples", code="LIMIT_EXCEEDED")


def check_work(request, width, height, samples, frames):
    """Refuse a job whose pixels x samples x frames exceed MAX_WORK (the effective scene values)."""
    work = width * height * max(1, samples) * max(1, frames)
    if work > MAX_WORK:
        raise JobRequestError(
            f"This job is too large: {width}x{height} at {samples:,} samples for {frames:,} frames. "
            "Ask the person whether to lower the resolution, samples or frame range, or to split it", code="LIMIT_EXCEEDED")
    return work
