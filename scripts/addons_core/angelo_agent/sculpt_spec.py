# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed sculpt contract and bounded arc-length sampling (no bpy dependency)."""

import math
import re
import uuid

MAX_VERTICES = 1000000
MAX_DABS = 512
MAX_PATHS = 32
BRUSHES = ("DRAW", "CLAY", "INFLATE", "SMOOTH", "CREASE", "PINCH", "GRAB", "FLATTEN", "MASK", "CLAY_STRIPS", "DRAW_SHARP", "SCRAPE", "ELASTIC_DEFORM", "POSE")
ANCHORED = frozenset({"GRAB", "ELASTIC_DEFORM", "POSE"})
FALLOFFS = ("SMOOTH", "SPHERE", "SHARP", "LINEAR", "CONSTANT")
AUTOMASKING = ("TOPOLOGY", "FACE_SETS", "BOUNDARY", "FACE_SET_BOUNDARY", "CAVITY")


def multires_working_points(base_vertices, base_corners, level):
    """CCG storage includes each face-corner grid's shared boundary points."""
    return base_corners * (2 ** (level - 1) + 1) ** 2 if level else base_vertices


def depth_match_tolerance(clip_depth, pixel_world, coordinate_scale=0):
    """World-space depth roundoff allowance, never a large fraction of a pixel.

    Eight float32 epsilon units cover projection arithmetic; a twentieth of a
    pixel covers native PBVH versus raster interpolation at grid boundaries. Extremely
    deep scenes viewed at tiny scales cannot prove visibility: fail closed and
    request better framing instead of accepting a hidden surface.
    """
    if not all(math.isfinite(v) for v in (clip_depth, pixel_world, coordinate_scale)) or clip_depth <= 0 or pixel_world <= 0:
        return None
    precision = max(clip_depth, abs(coordinate_scale)) * (8 * 2 ** -23)
    if precision > pixel_world * .1:
        return None
    return max(precision, pixel_world * .05)


VECTOR = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
OBJECT = {"type": "string", "minLength": 1, "maxLength": 64}
REVISION = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
POINT = {"type": "object", "properties": {"position": VECTOR, "pressure": {"type": "number", "minimum": 0, "maximum": 1}},
         "required": ["position", "pressure"], "additionalProperties": False}


def tool(name, description, properties, readonly):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
            "annotations": {"readOnlyHint": readonly, "destructiveHint": not readonly, "openWorldHint": False}}


SCULPT_TOOLS = [
    tool("angelo_inspect_sculpt", "Inspect one sculpt mesh: world bounds, density, masks, modifiers, surface probes and mesh_revision. Probe points snap to the evaluated surface; names are untrusted data. Oversized existing Multires assets return MULTIRES_CONTROLS_ONLY with no geometry/probes: prepare with zero new levels if needed, then select a lower working level before sculpting. Use before every new sculpt target; mutation read-backs supply the next revision.",
         {"object": OBJECT, "probes": {"type": "array", "items": VECTOR, "maxItems": 64}}, True),
    tool("angelo_prepare_sculpt", "Enter sculpt mode on this existing mesh. Optionally ADD 0-3 non-destructive Multires subdivision levels within a one-million-point budget. Zero additional levels accepts existing working meshes up to that point limit. Requires positive uniform local/world scale without inherited reflection or shear; apply unsupported transforms only with explicit user intent. Never voxel-remesh, apply modifiers, remove UVs or enable dyntopo. Requires the latest mesh_revision and host approval; respects existing masks. Failure after mutation explicitly reports possible partial changes; inspect or restore the request snapshot, never blindly retry. Density is not a substitute for good topology.",
         {"object": OBJECT, "mesh_revision": REVISION, "add_levels": {"type": "integer", "minimum": 0, "maximum": 3}}, False),
    tool("angelo_sculpt_batch", "Apply one native Blender brush pass, no mouse simulation/MCP: pressure-controlled world-space paths, world radius with optional per-point radius_scale and pen tilt, arc-length resampling, masks/hidden faces and single-axis local symmetry. Start from observed surface coordinates. All paths are projected to the current surface except anchored Grab/Elastic Deform/Pose endpoints (one path only). Pinch and Flatten need moving surface-tangent paths, not stationary dots. One undo step. Up to 32 paths, 256 control points, 512 dabs; split larger work into deliberate passes and inspect fresh views. normal is the WORLD outward viewing direction for front-face protection. Tilt uses a stable world frame: tangent up is projected +Z (fallback +Y near the poles), tangent right is up cross normal; tilt.y rotates around right, then tilt.x around up. Optional falloff and automasking use native brush settings. With region set, positions are world offsets from its live vertex landmark; the region radius bounds centers, not the entire falloff. Existing artist brush/preferences are preserved. Returns measured displacement and the next mesh_revision. Use moderate strengths, large-to-small forms; never claim visual quality without checking images.",
         {"object": OBJECT, "mesh_revision": REVISION, "brush": {"type": "string", "enum": list(BRUSHES)},
          "radius": {"type": "number", "minimum": 0.000001, "maximum": 1000},
          "strength": {"type": "number", "minimum": 0.001, "maximum": 1},
          "direction": {"type": "string", "enum": ["ADD", "SUBTRACT"]},
          "symmetry": {"type": "string", "enum": ["NONE", "X", "Y", "Z"]},
          "normal": VECTOR, "front_faces_only": {"type": "boolean"},
          "spacing": {"type": "number", "minimum": 0.1, "maximum": 0.5},
          "paths": {"type": "array", "minItems": 1, "maxItems": MAX_PATHS,
                    "items": {"type": "array", "minItems": 1, "maxItems": 256, "items": POINT}}}, False),
    tool("angelo_sculpt_view", "Read fresh native solid-shaded views of the sculpt target and send actual pixels to the model. No final rendering or scene/camera changes. Choose 1-3 view directions (FRONT = -Y, RIGHT = +X, TOP = +Z, BACK = +Y, THREE_QUARTER = [1,-1,0.7]). World-space orthographic framing and current mesh revision accompany each image; all visible scene geometry may appear. Read images before refining or judging the result.",
         {"object": OBJECT, "views": {"type": "array", "minItems": 1, "maxItems": 3,
                                      "items": {"type": "string", "enum": ["FRONT", "RIGHT", "TOP", "BACK", "THREE_QUARTER"]}},
          "resolution": {"type": "integer", "enum": [384, 512, 768]}}, True),
    tool("angelo_sculpt_target", "Turn points in a fresh native sculpt image into precise 3D surface hits on the target mesh. Use the image's exact view_reference ID and mesh_revision; don't guess depth or simulate clicks. pixels are normalized [x,y] from the image's TOP LEFT, each in [0,1], sampled at the nearest containing pixel center. Captured native depth rejects occluded or non-visible target surfaces, including visible neighbors/instances. Depth precision is bounded relative to the pixel footprint; an insufficient_depth_precision miss needs better framing, never a guessed hidden hit. Returns world position, outward normal and misses; use only visible hits as brush control points. Only this chat's admitted native observations are accepted, never attached reference images or arbitrary camera parameters.",
         {"object": OBJECT, "mesh_revision": REVISION, "view_reference": {"type": "string", "format": "uuid"},
          "pixels": {"type": "array", "minItems": 1, "maxItems": 64,
                     "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number", "minimum": 0, "maximum": 1}}}}, True),
]
# Extra controls are optional for saved calls from earlier toolset versions.
POINT["properties"].update(radius_scale={"type":"number", "minimum":.1, "maximum":2},
                           tilt={"type":"array", "items":{"type":"number", "minimum":-1, "maximum":1}, "minItems":2, "maxItems":2})
PASS_SCHEMA = next(t["inputSchema"] for t in SCULPT_TOOLS if t["name"] == "angelo_sculpt_batch")
PASS_SCHEMA["properties"].update(
    falloff={"type":"string", "enum":list(FALLOFFS)},
    automasking={"type":"array", "items":{"type":"string", "enum":list(AUTOMASKING)}, "maxItems":5, "uniqueItems":True},
    region={"type":"string", "maxLength":64})
STEP_SCHEMA = {"type":"object", "properties":{k:v for k,v in PASS_SCHEMA['properties'].items() if k not in {'object','mesh_revision'}},
               "required":[k for k in PASS_SCHEMA['required'] if k not in {'object','mesh_revision'}], "additionalProperties":False}
VIEW_SCHEMA = next(t["inputSchema"] for t in SCULPT_TOOLS if t["name"] == "angelo_sculpt_view")
VIEW_SCHEMA['properties'].update(framing_reference={"type":"string", "maxLength":36}, region={"type":"string", "maxLength":64})
SCULPT_TOOLS.extend([
    tool('angelo_sculpt_program', 'Run 1-8 different native brush passes in one bounded program, one dispatch and one Undo. Each pass follows the surface changed by the previous pass. Total work is limited to one ordinary pass budget; split larger work at visual checkpoints. Radius_scale and tilt are interpolated along paths. A named region makes all positions WORLD offsets from its moving landmark; region radius bounds stroke centers and its optional face set limits deformation. Failure may leave partial edits; inspect/Undo or restore the request checkpoint, never retry blindly.',
         {'object':OBJECT, 'mesh_revision':REVISION, 'steps':{'type':'array','minItems':1,'maxItems':8,'items':STEP_SCHEMA}}, False),
    tool('angelo_sculpt_region', 'Define or replace a named persistent surface landmark and region on this mesh. The landmark follows its working-surface vertex through sculpt deformation. The accepted vertex must be within half the requested radius; otherwise increase working resolution or choose an observed vertex. Legacy unvalidated anchors must be redefined. Radius bounds regional path centers. face_set=0 leaves existing masks unchanged; a positive ID binds an EXISTING face set and automatically enables native face-set masking for regional strokes. Topology/Multires level changes invalidate the anchor instead of guessing a new location. Definitions are saved in the blend file and undoable.',
         {'object':OBJECT,'mesh_revision':REVISION,'name':{'type':'string','minLength':1,'maxLength':64},
          'position':VECTOR,'radius':{'type':'number','minimum':.000001,'maximum':1000},'face_set':{'type':'integer','minimum':0,'maximum':2147483647}}, False),
    tool('angelo_sculpt_resolution', 'Select an EXISTING Multires sculpt working level without adding/deleting levels or applying modifiers. AUTO selects the lowest eligible level for the brush footprint and FORMS/SECONDARY/DETAIL stage within the working-point budget; EXPLICIT uses level. Returns actual read-back and a density warning if existing levels are insufficient. Reobserve the changed surface and redefine level-specific region anchors afterward.',
         {'object':OBJECT,'mesh_revision':REVISION,'mode':{'type':'string','enum':['AUTO','EXPLICIT']},
          'stage':{'type':'string','enum':['FORMS','SECONDARY','DETAIL']},'radius':{'type':'number','minimum':.000001,'maximum':1000},
          'level':{'type':'integer','minimum':0,'maximum':12}}, False),
    tool('angelo_sculpt_review', 'Capture the exact camera of a previous admitted native view for before/after comparison. Returns actual pixels, target-only silhouette area/IoU, and errors against optional reference outline and named landmark image targets. Outline/pixels are normalized top-left coordinates in THIS framing; trace the intended reference yourself, never mistake a user photo for a native targeting camera. Metrics measure these explicit targets, not artistic quality. Occlusion/framing changes require visual judgment. A baseline alone measures change, not improvement.',
         {'object':OBJECT, 'baseline':{'type':'string','format':'uuid'},
          'outline':{'type':'array','maxItems':128,'items':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'number','minimum':0,'maximum':1}}},
          'landmarks':{'type':'array','maxItems':32,'items':{'type':'object','properties':{'region':{'type':'string','minLength':1,'maxLength':64},
                        'pixel':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'number','minimum':0,'maximum':1}}},'required':['region','pixel'],'additionalProperties':False}}}, True),
])
SCULPT_NAMES = frozenset(t["name"] for t in SCULPT_TOOLS)
SCULPT_MUTATIONS = frozenset(t['name'] for t in SCULPT_TOOLS if not t['annotations']['readOnlyHint'])


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Use a finite number between {low:g} and {high:g}")
    return float(value)


def vector(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Surface coordinates require three finite numbers")
    return [number(x, -100000, 100000) for x in value]


def validate_sculpt(name, args):
    obj = args["object"]
    if not isinstance(obj, str) or not obj.strip() or len(obj) > 64 or any(ord(c) < 32 for c in obj):
        raise ValueError("Object names must be 1-64 printable characters")
    result = dict(args)
    if "mesh_revision" in args and (not isinstance(args["mesh_revision"], str) or not re.fullmatch("[a-f0-9]{64}", args["mesh_revision"])):
        raise ValueError("Inspect the sculpt target and use its exact mesh_revision")
    if name == "angelo_inspect_sculpt":
        if not isinstance(args["probes"], list) or len(args["probes"]) > 64:
            raise ValueError("Use up to 64 surface probes")
        result["probes"] = [vector(v) for v in args["probes"]]
    elif name == "angelo_prepare_sculpt":
        if type(args["add_levels"]) is not int or not 0 <= args["add_levels"] <= 3:
            raise ValueError("Add 0-3 non-destructive subdivision levels")
    elif name == 'angelo_sculpt_program':
        if not isinstance(args['steps'], list) or not 1 <= len(args['steps']) <= 8:
            raise ValueError('A sculpt program requires 1-8 passes')
        result['steps'] = []
        total = 0
        for step in args['steps']:
            if not isinstance(step, dict) or set(step)-set(STEP_SCHEMA['properties']) or set(STEP_SCHEMA['required'])-set(step):
                raise ValueError('Unexpected or missing sculpt program control')
            checked = validate_sculpt('angelo_sculpt_batch', dict(step, object=obj, mesh_revision=args['mesh_revision']))
            total += sum(map(len, resample_paths(checked['paths'], checked['radius']*checked['spacing']*min(p.get('radius_scale',1) for path in checked['paths'] for p in path), grab=checked['brush'] in ANCHORED)))
            result['steps'].append({k:v for k,v in checked.items() if k not in {'object','mesh_revision'}})
        if total > MAX_DABS:
            raise ValueError('A whole sculpt program is limited to 512 dabs')
    elif name == 'angelo_sculpt_region':
        result['name'] = label(args['name'])
        result['position'] = vector(args['position'])
        result['radius'] = number(args['radius'],1e-6,1000)
        if type(args['face_set']) is not int or not 0 <= args['face_set'] <= 2147483647:
            raise ValueError('Use an existing nonnegative face-set ID')
    elif name == 'angelo_sculpt_resolution':
        if args['mode'] not in ('AUTO','EXPLICIT') or args['stage'] not in ('FORMS','SECONDARY','DETAIL'):
            raise ValueError('Choose a working resolution mode and sculpt stage')
        if type(args['level']) is not int or not 0 <= args['level'] <= 12:
            raise ValueError('Working level must be an integer from 0 to 12')
        result['radius'] = number(args['radius'],1e-6,1000)
    elif name == 'angelo_sculpt_review':
        result['baseline'] = str(uuid.UUID(args['baseline']))
        if not isinstance(args['outline'],list) or len(args['outline']) > 128 or 0 < len(args['outline']) < 3:
            raise ValueError('Provide no outline or a polygon of 3-128 points')
        result['outline'] = [validated_pixel(v) for v in args['outline']]
        if not isinstance(args['landmarks'],list) or len(args['landmarks']) > 32:
            raise ValueError('Use at most 32 reference landmarks')
        result['landmarks'] = []
        for item in args['landmarks']:
            if not isinstance(item,dict) or set(item) != {'region','pixel'}:
                raise ValueError('A reference landmark needs region and pixel')
            result['landmarks'].append({'region':label(item['region']), 'pixel':validated_pixel(item['pixel'])})
    elif name == "angelo_sculpt_view":
        if type(args["resolution"]) is not int or args["resolution"] not in (384, 512, 768):
            raise ValueError("Sculpt views are limited to 384, 512 or 768 pixels")
        views = args["views"]
        if not isinstance(views, list) or not 1 <= len(views) <= 3 or any(type(v) is not str or v not in ("FRONT", "RIGHT", "TOP", "BACK", "THREE_QUARTER") for v in views):
            raise ValueError("Choose 1-3 named sculpt views")
        result["views"] = list(views)
        if 'framing_reference' in args and type(args['framing_reference']) is not str:
            raise ValueError('Framing references must be native view IDs')
        if args.get('framing_reference'):
            result['framing_reference'] = str(uuid.UUID(args['framing_reference']))
        if 'region' in args and type(args['region']) is not str:
            raise ValueError('Region names must be text')
        if args.get('region'):
            result['region'] = label(args['region'])
    elif name == 'angelo_sculpt_target':
        try:
            result['view_reference'] = str(uuid.UUID(args['view_reference']))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Choose an exact native sculpt view reference ID') from None
        pixels = args['pixels']
        if not isinstance(pixels, list) or not 1 <= len(pixels) <= 64:
            raise ValueError('Use 1-64 image-space surface probes')
        result['pixels'] = []
        for pixel in pixels:
            if not isinstance(pixel, list) or len(pixel) != 2:
                raise ValueError('Image-space probes require [x,y] coordinates')
            result['pixels'].append([number(v, 0, 1) for v in pixel])
    elif name == "angelo_sculpt_batch":
        for field, values in (("brush", BRUSHES), ("direction", ("ADD", "SUBTRACT")), ("symmetry", ("NONE", "X", "Y", "Z"))):
            if type(args[field]) is not str or args[field] not in values:
                raise ValueError("Unknown sculpt " + field)
        result["radius"] = number(args["radius"], 1e-6, 1000)
        result["strength"] = number(args["strength"], .001, 1)
        result["spacing"] = number(args["spacing"], .1, .5)
        result["normal"] = vector(args["normal"])
        length = math.sqrt(sum(x*x for x in result["normal"]))
        if length < 1e-8:
            raise ValueError("The outward viewing normal cannot be zero")
        result["normal"] = [x / length for x in result["normal"]]
        if type(args["front_faces_only"]) is not bool:
            raise ValueError("Front face protection must be a boolean")
        result['falloff'] = args.get('falloff','SMOOTH')
        if result['falloff'] not in FALLOFFS:
            raise ValueError('Unknown sculpt falloff')
        masks = args.get('automasking',[])
        if not isinstance(masks,list) or len(masks)>5 or any(type(v) is not str or v not in AUTOMASKING for v in masks) or len(set(masks)) != len(masks):
            raise ValueError('Choose distinct native automasking options')
        result['automasking'] = list(masks)
        if 'region' in args and type(args['region']) is not str:
            raise ValueError('Region names must be text')
        result['region'] = label(args['region']) if args.get('region') else ''
        paths = args["paths"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_PATHS:
            raise ValueError("A sculpt pass requires 1-32 paths")
        copied = []
        for path in paths:
            if not isinstance(path, list) or not 1 <= len(path) <= 256:
                raise ValueError("Every sculpt path needs 1-256 control points")
            values = []
            for point in path:
                if not isinstance(point, dict) or set(point)-{"position", "pressure", "radius_scale", "tilt"} or not {"position","pressure"} <= set(point):
                    raise ValueError("Sculpt control points contain only position and pressure")
                values.append({"position": vector(point["position"]), "pressure": number(point["pressure"], 0, 1)})
                if 'radius_scale' in point:
                    values[-1]['radius_scale'] = number(point['radius_scale'],.1,2)
                if 'tilt' in point:
                    if not isinstance(point['tilt'],list) or len(point['tilt']) != 2:
                        raise ValueError('Tilt needs two normalized pen components')
                    values[-1]['tilt'] = [number(v,-1,1) for v in point['tilt']]
            copied.append(values)
        if sum(map(len, copied)) > 256:
            raise ValueError("Use at most 256 control points per pass")
        if args["brush"] in ANCHORED and (len(copied) != 1 or len(copied[0]) < 2):
            raise ValueError("Grab requires one path with a surface start and at least one endpoint")
        if args['brush'] in {'PINCH', 'FLATTEN', 'SCRAPE', 'CLAY_STRIPS'} and any(len(p) < 2 or not any(
                math.dist(a['position'], b['position']) > 1e-9 for a,b in zip(p,p[1:])) for p in copied):
            raise ValueError('Pinch and Flatten need moving surface-tangent paths, not stationary dots')
        result["paths"] = copied
        resample_paths(copied, result["radius"] * result["spacing"] * min(p.get("radius_scale",1) for path in copied for p in path), grab=args["brush"] in ANCHORED)
    return result


def resample_paths(paths, step, *, grab=False):
    """Even arc-length pressure interpolation, independent of viewport/zoom.

    Always include endpoints. Grab is an anchored deformation, not a chain of
    additive dabs, so only its start and final endpoint are needed.
    """
    output = []
    count = 0
    for path in paths:
        if grab:
            sampled = [path[0], path[-1]]
        else:
            distances = [math.dist(a["position"], b["position"]) for a, b in zip(path, path[1:])]
            total = sum(distances)
            intervals = max(1, math.ceil(total / step)) if total > 1e-12 else 0
            if count + intervals + 1 > MAX_DABS:
                raise ValueError("This pass exceeds 512 native dabs; shorten paths or split into smaller passes")
            sampled = [dict(path[0], position=list(path[0]["position"]))]
            segment, origin = 0, 0.0
            for i in range(1, intervals + 1):
                distance = total * i / intervals
                while segment < len(distances) - 1 and origin + distances[segment] < distance:
                    origin += distances[segment]
                    segment += 1
                span = distances[segment]
                t = min(1.0, max(0.0, (distance - origin) / span)) if span > 1e-12 else 1.0
                a, b = path[segment], path[segment + 1]
                sampled.append({"position": [x + (y-x)*t for x, y in zip(a["position"], b["position"])],
                                "pressure": a["pressure"] + (b["pressure"]-a["pressure"])*t})
                if 'radius_scale' in a or 'radius_scale' in b:
                    sampled[-1]['radius_scale'] = a.get('radius_scale',1)+(b.get('radius_scale',1)-a.get('radius_scale',1))*t
                if 'tilt' in a or 'tilt' in b:
                    sampled[-1]['tilt'] = [x+(y-x)*t for x,y in zip(a.get('tilt',[0,0]),b.get('tilt',[0,0]))]
        count += len(sampled)
        if count > MAX_DABS:
            raise ValueError("Split this sculpt into passes of at most 512 native dabs")
        output.append(sampled)
    return output


def label(value):
    if not isinstance(value,str) or not value.strip() or len(value)>64 or any(ord(c)<32 for c in value):
        raise ValueError('Use a short printable region name')
    return value.strip()


def validated_pixel(value):
    if not isinstance(value,list) or len(value)!=2:
        raise ValueError('Image targets require [x,y]')
    return [number(v,0,1) for v in value]


def choose_level(levels, stage, radius, diagonal, counts):
    """Lowest existing level with enough samples across the brush footprint."""
    desired = max(1, (({'FORMS':3,'SECONDARY':5,'DETAIL':8}[stage] * diagonal) / radius)**2)
    allowed = [level for level in range(levels+1) if counts[level] <= MAX_VERTICES]
    if not allowed:
        raise ValueError('No existing Multires level fits the working-point budget')
    selected = next((level for level in allowed if counts[level] >= desired), allowed[-1])
    return selected, counts[selected] >= desired
