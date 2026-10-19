# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Island scenario: a realistic floating island at first light, rendered in Cycles.

Eight turns, the way a creative director would brief a hero still: blockout,
camera and light; rock form; rock material with true displacement; scattered
vegetation; water and a waterfall; volumetric atmosphere; light and look; and
the final frame as a background render job. Unlike the bridge, the prompts
direct a look, not dimensions, and attach no reference image; only the scale,
the camera and the object names are fixed, so the closed checks stay loose:
they ask for what any acceptable build must have (the named objects, real
geometric detail, a displacing material, a scatter, volumes, a low sun in a
physical sky, depth of field and a finished 4K render file), never for how it
looks. People judge the picture; ``doc/angelo/scenario_benchmarks.rst``
describes the scenario, its checks and how it runs.

``build(bpy, upto)`` is a deliberately plain ground truth: the same named
objects as simple shapes, enough to pass every check, render references and
score silhouettes. It is not a target look.

Axes: the camera looks along +Y from the -Y side, so -X is frame left and +X
frame right; Z is up; metres. The plateau's top is near z = 0.
"""

import math

import scenario_build_kit as kit

ID = "island"
TITLE = "Realistic floating island at first light"
FRAME = 1

ISLAND_SIZE, CLIFF_Z, KEEL_Z, HIGH_GROUND = (44.0, 30.0), -8.0, -20.0, 2.5
POND_CENTRE, POND_SIZE, NOTCH = (-11.0, -2.0), (9.0, 7.0), (-21.0, 1.0)   # The notch is the island's left-hand tip.
PINES = (((8.0, 3.0), 9.5), ((14.0, -2.0), 8.0), ((5.0, 8.0), 10.5))   # ((x, y), height)
FALL_HEIGHT = 20.0
HERO = {"location": (-11.5, -128.0, 18.0), "target": (-9.0, 0.0, 2.5), "lens": 50.0}
SUN_ELEVATION, SUN_AZIMUTH = 3.0, 160.0   # First light: from frame left, about 20 degrees behind the island.
FINAL_RENDER = {"name": "island-final.png", "size": (3840, 2160)}
# Extra tools this scenario's tester approves: the render job the last turn asks for.
APPROVABLE = ("angelo_job_start",)
# How long the driver waits for a background job the turn started before grading it.
JOB_WAIT_S = 3 * 3600.0

CAMERAS = {
    "front": {"location": (0.0, -160.0, -8.0), "target": (0.0, 0.0, -8.0), "ortho_scale": 72.0, "role": "reference",
              "label": "Front (orthographic, looking +Y)"},
    "side": {"location": (150.0, -30.0, 14.0), "target": (0.0, 0.0, -7.0), "lens": 50.0, "role": "reference",
             "label": "Side (perspective, from +X)"},
    "top": {"location": (0.0, 0.0, 120.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 64.0, "role": "reference",
            "label": "Top (orthographic, +Y up)"},
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "closeup_fall": {"location": (-52.0, -44.0, 6.0), "target": (-21.0, 1.0, -8.0), "lens": 50.0, "role": "closeup",
                     "label": "Close-up: the left tip and the waterfall"},
    "closeup_pines": {"location": (30.0, -34.0, 9.0), "target": (9.0, 2.0, 4.5), "lens": 50.0, "role": "closeup",
                      "label": "Close-up: the pines and the plateau"},
}
ISLAND, POND, PINE, GRASS = {"name": "Island"}, {"name": "Pond"}, {"prefix": "Pine"}, {"name": "Grass"}
WATERFALL, MIST, CLOUD, ISLET = {"name": "Waterfall"}, {"name": "Mist"}, {"prefix": "Cloud"}, {"prefix": "Islet"}
BOULDER, SHARD, BIRDS, ROOTS = {"prefix": "Boulder"}, {"prefix": "Shard"}, {"name": "Birds"}, {"name": "Roots"}
CAMERA, SUN = {"name": "Hero Camera"}, {"name": "Sun"}
SUBJECT = (ISLAND, PINE, WATERFALL, BOULDER, SHARD)

TURNS = [
    {"id": "blockout", "title": "Blockout, camera and first light", "references": [],
     "prompt": "We're making one hero still: a realistic floating island at first light, rendered in Cycles at 4K. "
               "Metres, Z up; the camera looks along +Y, so -X is frame left. I'll direct it in stages and judge "
               "each one from a Cycles render, which you can't see yourself.\n\n"
               "How we work: name things exactly as I do. Keep it procedural and light in the viewport. Use reviewed "
               "Python wherever your typed tools fall short; I read and approve every script. One focused script per "
               "step and a short reply each time, and keep going step by step until the stage is done. Never render "
               "inside a script, and don't add verification checks of your own: I judge by eye.\n\n"
               "Stage 1, blockout and light. Plain grey shapes, no detail yet:\n"
               "- Remove the default Cube and Light. Create 'Hero Camera' (full frame, 36 mm sensor, 50 mm lens) at "
               "(-11.5, -128, 18), looking at (-9, 0, 2.5), make it the scene camera, and only then remove the "
               "default Camera.\n"
               "- 'Island': one closed mesh with enough even topology to subdivide and displace later. An irregular "
               "plateau about 44 m across in X and 30 m in Y, its top near z = 0 with gentle relief and higher "
               "ground (to about 2.5 m) on the +X half. The top 8 m is a near-vertical cliff wall; below it an "
               "asymmetric underside tapers to a ragged keel no lower than z = -20 m, with two secondary hanging "
               "lobes. Not a cone, not symmetric.\n"
               "- A shallow pond basin on the left half of the plateau, around (-11, -2), about 9 by 7 m, and a "
               "notch in the rim at the island's left-hand tip with a short channel running to it from the pond: "
               "the water will fall there, in profile from the camera. Put a flat placeholder 'Pond' in the basin.\n"
               "- 'Pine 1', 'Pine 2' and 'Pine 3': simple stand-ins 7 to 11 m tall on the high ground, around "
               "(8, 3), (14, -2) and (5, 8).\n"
               "- First light: a physical sky (multiple scattering) a few minutes after sunrise, with the sun 3 "
               "degrees above the horizon, coming from frame left and about 20 degrees behind the island. Deep "
               "blue overhead, through violet to a band of peach at the horizon. 'Sun': a sun light in the same "
               "direction, warm pink-gold, doing the direct light and the shadows. AgX with the Medium High "
               "Contrast look, exposed for a dawn that is dark but not murky.\n"
               "- Scene: Cycles on the GPU with denoising, 1920 x 1080 for now.\n"
               "The camera stays fixed from here on.",
     "checks": [
         {"kind": "object", "target": {"name": "Cube"}, "exists": False, "label": "default cube removed"},
         {"kind": "object", "target": ISLAND, "type": "MESH", "label": "Island exists"},
         {"kind": "bbox", "target": ISLAND, "size": [[34, 56], [22, 40], [16, 34]], "label": "Island about 44 x 30 m"},
         {"kind": "bbox", "target": ISLAND, "max": [None, None, 2.0], "tolerance": 3.5,
          "label": "plateau top near z = 0"},
         {"kind": "object", "target": POND, "label": "Pond placeholder"},
         {"kind": "count", "target": PINE, "count": [3, 400], "label": "three pines"},
         {"kind": "camera", "target": CAMERA, "active": True, "location": list(HERO["location"]), "radius": 4.0,
          "look_at": list(HERO["target"]), "min_dot": 0.995, "lens": [48.0, 52.0], "sensor": [35.0, 37.0],
          "label": "Hero Camera placed, aimed and active"},
         {"kind": "light", "target": SUN, "type": "SUN", "elevation_deg": [1, 9], "label": "Sun just above the horizon"},
         {"kind": "world", "sky_or_color": ["warm", "blue"], "sun_elevation_deg": [0.5, 9],
          "label": "physical sky at first light"},
         {"kind": "scene", "values": {"render.engine": "CYCLES", "view_settings.view_transform": "AgX"},
          "label": "Cycles and AgX"},
     ]},
    {"id": "rock_form", "title": "Rock form", "references": [],
     "prompt": "Stage 2, the rock's form. Real geometry, built non-destructively (a subdivision level, then geometry "
               "nodes or modifiers; modest in the viewport, higher at render):\n"
               "- Bedding: sandstone beds of very uneven thickness, thin bundles of 0.3 to 1 m between a few "
               "massive beds 3 to 6 m thick, tilted a few degrees and warped. Use hard, cell-like functions so the "
               "beds step in and out with sharp edges. Nothing smooth or rolling: that reads as melted wax.\n"
               "- Jointing: a few vertical joints that cut the wall into large uneven blocks; some blocks stand "
               "proud, a few have fallen away. Long continuous ledges, not masonry.\n"
               "- Large relief: two or three buttresses that push out 2 to 3 m, with deep bays between them, so "
               "the outline is not an even ring. The rim is broken into blocks and notches, with the soil line a "
               "little back from the rock edge, never one clean overhanging lip.\n"
               "- Underside: below the 8 m wall the rock is undercut, then hangs in tapering fins, broken pillars "
               "and two distinct lobes, irregular and eroded, no stepped stacks of boxes. Nothing lower than "
               "z = -20 m.\n"
               "- 'Shard 1' to 'Shard 5': detached lumps of the same rock hanging close under the keel and the "
               "lobes, two big (5 to 6 m) and three small. Rough irregular lumps, dense enough to displace; not "
               "boxes and not spikes.\n"
               "- 'Boulder 1' to 'Boulder 6': weathered boulders half sunk in the plateau near the cliff edge, "
               "smooth-shaded.\n"
               "The plateau stays walkable, with the pond basin, the channel and the notch intact. Check it "
               "through the hero camera with your view tool as you go.",
     "checks": [
         {"kind": "geometry", "target": ISLAND, "vertices": [20000, 1.0e8], "label": "Island has real geometric detail"},
         {"kind": "bbox", "target": ISLAND, "size": [[34, 60], [22, 44], [16, 38]],
          "label": "Island keeps its size"},
         {"kind": "count", "target": BOULDER, "count": [3, 400], "label": "boulders on the plateau"},
         {"kind": "count", "target": SHARD, "count": [2, 400], "label": "detached shards below"},
     ]},
    {"id": "rock_material", "title": "Rock material", "references": [],
     "prompt": "Stage 3, the rock material. This is where the rock becomes real. Fully procedural, no image "
               "textures:\n"
               "- True displacement in Cycles (displacement and bump), with adaptive subdivision on 'Island' as its "
               "last modifier, diced finely enough for 4K. Let the big breaks be up to a metre deep, so the "
               "silhouette gets real ledges and notches.\n"
               "- Height, layered from sharp functions: (1) bedding on the same tilted, warped coordinate as the "
               "geometry: stepped ledges, some beds standing proud as rounded ledges that catch the light, "
               "smoother faces on the massive beds, shaly bundles between them; (2) fracture in three tiers: a "
               "few big joints through several beds, medium breaks that stop at bed boundaries, sparse fine "
               "chips; (3) vertical weathering streaks and gullies; (4) pitting and fine grain. Grooves and cracks "
               "have uneven depth and width and start and stop; never a regular net of thin lines, which reads as "
               "ruled panel lines.\n"
               "- Below the cliff band, erode the bedding steps, so the hanging masses taper into uneven ledges "
               "instead of stacking like a ziggurat.\n"
               "- Colour: dark, rich sandstone. Warm ochre-brown mid values, with darker iron-stained beds and a "
               "few paler ones as subtle shifts broken up by noise, never flat stripes; dark varnish streaks and "
               "rust bleeding down across the beds; grey-green lichen patches on the upper faces; a clearly "
               "darker, cooler slate underside. Paint no darkening into the cracks: relief and shadow do that. "
               "The rock must sit darker than the cloud tops will, and take warm light as colour, never as white.\n"
               "- Plateau top: dark damp earth with low hummocks and hollows (0.3 to 1 m) and a few low rounded "
               "rock outcrops near the rim.\n"
               "- Rough (0.65 to 0.9), never glossy. The island's own material runs through the shards at full "
               "displacement; the boulders take a smaller-scale version.",
     "checks": [
         {"kind": "displacement", "target": ISLAND, "label": "Island material has true displacement"},
         {"kind": "count", "target": BOULDER, "count": [3, 400], "label": "boulders kept"},
     ]},
    {"id": "vegetation", "title": "Vegetation", "references": [],
     "prompt": "Stage 4, vegetation.\n"
               "- 'Grass': a geometry-nodes scatter on the plateau. Several blade-clump variants 0.2 to 0.5 m tall, "
               "dense on the flats, thinner toward the rim, the pond margin and under the pines, none on bare rock, "
               "in the water or in the channel; random rotation, scale and lean, with a slight overall lean "
               "down-wind, to the right. Muted olive and sage with dry straw tips, darker at the roots, and some "
               "translucency. Add patches of low heather-like shrubs and a few tufts on the cliff's ledges. Show "
               "fewer instances in the viewport than in the render.\n"
               "- 'Pine 1' to 'Pine 3': replace the stand-ins with believable wind-bent Scots pines, 7 to 11 m "
               "tall, no two alike. A leaning, lumpy trunk with real bark relief, grey-brown low down and orange in "
               "the upper trunk and limbs; limbs, branches and twigs you can see; and the foliage as tens of "
               "thousands of fine needle shoots (sprays of thin needles, never cards or blobs), clumped into dense "
               "irregular masses at different heights with sky showing between them, heavier on the down-wind "
               "side. From the hero camera each crown must read as a dark, full, wind-shaped mass.\n"
               "- 'Roots': a few gnarled roots (4 to 10 cm thick) and thin vines that creep down the rock from "
               "under the rim, clinging for most of their length with only their ends hanging free, and some from "
               "the underside's ledges.",
     "checks": [
         {"kind": "modifier", "target": GRASS, "type": "NODES", "label": "Grass is a geometry-nodes scatter"},
         # The prompt asks for fewer instances in the viewport than at render, and checks see the viewport.
         {"kind": "instances", "target": GRASS, "count": [800, 1.0e9],
          "label": "a real grass scatter (800 or more instances in the viewport)"},
         {"kind": "count", "target": PINE, "count": [3, 400], "label": "three pines kept"},
         {"kind": "bbox", "target": PINE, "size": [None, None, [6.0, 18.0]], "label": "pines 7 to 11 m tall"},
         {"kind": "object", "target": ROOTS, "label": "roots on the rock"},
     ]},
    {"id": "water", "title": "Water and waterfall", "references": [],
     "prompt": "Stage 5, water.\n"
               "- 'Pond': still, dark water filling the basin to just below the turf, mirror-like, with faint wind "
               "ripples in patches, a wet margin and a few stones breaking the surface ('Pond Stones').\n"
               "- 'Waterfall': the water runs down the channel and leaves the notch at the left-hand tip, arcing "
               "out to the left and falling about 20 m clear of the rock, seen in profile against the clouds. A "
               "thin glassy sheet at the lip that separates into strands and veils with gaps between them, then "
               "into thousands of tiny, round, soft droplets, stretched a little along their fall. It widens, thins "
               "and fades out completely by the level of the keel. White and slightly translucent where it is "
               "aerated. Never a solid ribbon, never square particles.\n"
               "- 'Mist': a volumetric plume of fine mist around the lower two thirds of the fall, drifting left "
               "and down, soft-edged, and dense enough to glow clearly where the low sun comes through it.\n"
               "- Wet, darker rock around the lip, behind the fall and round the pond.",
     "checks": [
         {"kind": "object", "target": WATERFALL, "label": "Waterfall exists"},
         {"kind": "bbox", "target": WATERFALL, "size": [None, None, [10.0, 36.0]], "label": "the fall drops about 20 m"},
         {"kind": "volume", "target": MIST, "label": "Mist is a volume"},
         {"kind": "object", "target": POND, "label": "Pond kept"},
     ]},
    {"id": "atmosphere", "title": "Atmosphere and depth", "references": [],
     "prompt": "Stage 6, atmosphere and depth. It has to stay cheap to render: a 1280 x 720 preview at 96 samples "
               "should finish in about two minutes on this Mac's GPU.\n"
               "- 'Cloud Deck': a cumulus sea far below, as a volume only within about 2.5 km of the camera, its "
               "tops between about z = -115 and -60 m. Build it from puffy, cell-like noise at several scales: a "
               "few big soft banks and towers, calmer flat stretches between them, soft wispy edges and thin "
               "veils, and real gaps between the banks. Dense enough for bright tops and shadowed hollows.\n"
               "- 'Cloud Deck Far': beyond that, the same deck continued to about 11 km as a cheap displaced "
               "surface, with gaps.\n"
               "- 'Cloud Deck Low': a second, calmer, dimmer cloud layer a few hundred metres further down, so "
               "the gaps open onto deeper cloud, never onto empty space.\n"
               "- 'Horizon Haze': a low band of haze far away, so the deck fades softly into the sky before the "
               "horizon. 'Haze': thin haze behind the island, so distant things fade while the island stays "
               "crisp.\n"
               "- 'Cloud Wisp 1' to 'Cloud Wisp 4': thin wisps below and behind the island, none in front of the "
               "rock.\n"
               "- 'Islet 1' and 'Islet 2': two smaller rock islands of the same rock, 500 to 700 m away and "
               "clearly different in shape from the hero: one tall and narrow on the left, one low and broad in "
               "the right background.\n"
               "- 'Birds': a dozen birds wheeling to the left of the fall, very small.\n"
               "Raise the camera's far clip to cover all of it.",
     "checks": [
         {"kind": "volume", "target": CLOUD, "label": "volumetric clouds"},
         {"kind": "count", "target": CLOUD, "count": [3, 400], "label": "cloud layers and wisps"},
         {"kind": "count", "target": ISLET, "count": [2, 400], "label": "distant islets"},
         {"kind": "object", "target": BIRDS, "label": "birds for scale"},
     ]},
    {"id": "lighting", "title": "Light and look", "references": [],
     "prompt": "Stage 7, light and look. Keep the dawn sky and the cool, quiet world, and make the island sing in "
               "it.\n"
               "- The island's left-facing cliff faces, the left ends of its ledges, the grass, the pine crowns "
               "and the waterfall should clearly catch the pink-gold first light, while its front stays in cool "
               "blue shade with readable detail. If the sun is too weak for that without lifting the clouds, give "
               "the island its own matching boost light: same direction and colour, linked only to the island and "
               "to what grows and flows on it.\n"
               "- Balance the exposure so the cloud tops and the sky stay unclipped and the shaded front of the "
               "island sits in the lower mid values, with grounded blacks only in the deep recesses.\n"
               "- Set the scene to its final size, 3840 x 2160, with sampling for a clean 4K frame: denoising on, "
               "enough volume bounces for the clouds and enough transparent bounces for the fall and the "
               "needles.",
     "checks": [
         {"kind": "scene", "values": {"render.resolution_x": 3840, "render.resolution_y": 2160,
                                      "render.resolution_percentage": 100}, "label": "final size 3840 x 2160"},
         {"kind": "light", "target": SUN, "type": "SUN", "elevation_deg": [1, 9], "label": "Sun stays low"},
         {"kind": "world", "sky_or_color": ["warm", "blue"], "sun_elevation_deg": [0.5, 9],
          "label": "dawn sky kept"},
         {"kind": "scene", "values": {"render.engine": "CYCLES", "view_settings.view_transform": "AgX"},
          "label": "Cycles and AgX"},
     ]},
    {"id": "final_frame", "title": "Final frame", "references": [],
     "prompt": "Stage 8, the final frame. Turn on depth of field on 'Hero Camera': f/2.8, focused on the island's "
               "near rim. Then render the frame as a background job: Cycles, 3840 x 2160, PNG, saved as "
               "renders/island-final.png, with a time budget of three hours.",
     "checks": [
         {"kind": "camera", "target": CAMERA, "active": True, "dof": True, "fstop": [1.2, 5.6],
          "focus_distance": [85.0, 150.0], "label": "depth of field on, focused on the island"},
     ],
     "outputs": [{"name": FINAL_RENDER["name"], "min_size": list(FINAL_RENDER["size"]),
                  "label": "the 4K render was written"}],
     "job": {"kind": "RENDER_STILL", "output": "renders/" + FINAL_RENDER["name"],
             "resolution": list(FINAL_RENDER["size"]), "samples": 4, "format": "PNG", "minutes": 60}},
]


# ------------------------------------------------------------------ ground truth (plain stand-ins)

def _outline(theta):
    """Plateau radius factor: an irregular outline, the same every time."""
    return 1.0 + 0.11 * math.sin(2 * theta + 0.6) + 0.07 * math.sin(3 * theta + 1.9) + 0.04 * math.sin(5 * theta)


def top_height(x, y):
    """The plateau: nearly flat, higher on the +X half, a shallow basin where the pond sits."""
    rise = HIGH_GROUND * max(0.0, min(1.0, (x + 2.0) / 18.0)) * 0.8
    px, py = (x - POND_CENTRE[0]) / (POND_SIZE[0] / 2), (y - POND_CENTRE[1]) / (POND_SIZE[1] / 2)
    basin = -0.7 if px * px + py * py < 1.0 else 0.0
    return rise + basin


# (z, radius factor, centre offset): the cliff band, then an off-centre taper to the keel.
_PROFILE = ((0.0, 1.0, (0.0, 0.0)), (-3.0, 0.98, (0.0, 0.0)), (-6.0, 0.99, (0.2, 0.0)), (CLIFF_Z, 0.94, (0.4, 0.2)),
            (-10.5, 0.72, (1.2, 0.6)), (-13.5, 0.5, (2.2, 0.9)), (-16.5, 0.3, (3.0, 1.0)), (-18.8, 0.14, (3.4, 0.8)))


def island_geometry(segments=128, top_rings=6):
    """A closed lathe-like body: concentric top rings following ``top_height``, then the profile, then the keel."""
    vertices, faces = [], []
    rx, ry = ISLAND_SIZE[0] / 2, ISLAND_SIZE[1] / 2

    def ring(scale, z, offset=(0.0, 0.0), on_top=False):
        start = len(vertices)
        for index in range(segments):
            theta = 2 * math.pi * index / segments
            x = offset[0] + rx * scale * _outline(theta) * math.cos(theta)
            y = offset[1] + ry * scale * _outline(theta) * math.sin(theta)
            vertices.append((x, y, top_height(x, y) if on_top else z))
        return start

    def bridge(a, b, flip=False):
        for index in range(segments):
            following = (index + 1) % segments
            quad = (a + index, a + following, b + following, b + index)
            faces.append(quad[::-1] if flip else quad)

    centre = len(vertices)
    vertices.append((0.0, 0.0, top_height(0.0, 0.0)))
    rings = [ring(k / top_rings, 0.0, on_top=True) for k in range(1, top_rings + 1)]
    for index in range(segments):
        faces.append((centre, rings[0] + index, rings[0] + (index + 1) % segments))
    for inner, outer in zip(rings, rings[1:]):
        bridge(inner, outer, flip=True)
    previous = rings[-1]
    for z, scale, offset in _PROFILE[1:]:
        current = ring(scale, z, offset)
        bridge(previous, current, flip=True)
        previous = current
    keel = len(vertices)
    vertices.append((3.6, 0.7, KEEL_Z))
    for index in range(segments):
        faces.append((keel, previous + (index + 1) % segments, previous + index))
    return vertices, faces


def _material(bpy, name, color, roughness=0.85):
    return bpy.data.materials.get(name) or kit.principled(bpy, name, color, roughness)


def _volume_material(bpy, name, density, color=(0.9, 0.9, 0.9), anisotropy=0.4):
    """A volume-only material: Principled Volume into the output's Volume socket, no surface."""
    material = bpy.data.materials.new(name)
    if hasattr(material, "use_nodes"):
        material.use_nodes = True
    tree = material.node_tree
    for node in [n for n in tree.nodes if n.type != "OUTPUT_MATERIAL"]:
        tree.nodes.remove(node)
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    volume = tree.nodes.new("ShaderNodeVolumePrincipled")
    volume.inputs["Color"].default_value = (*color, 1.0)
    volume.inputs["Density"].default_value = density
    volume.inputs["Anisotropy"].default_value = anisotropy
    tree.links.new(volume.outputs["Volume"], output.inputs["Volume"])
    return material


def _replace(bpy, name):
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


def _pine_stand_in(bpy, index, position, height):
    """Trunk and a cone: the blockout's stand-in."""
    x, y = position
    base = top_height(x, y)
    trunk, faces = kit.cylinder_geometry(0.3, height * 0.45, 10, centre=(x, y, base + height * 0.225))
    crown = [(x + 2.2 * math.cos(2 * math.pi * k / 10), y + 2.2 * math.sin(2 * math.pi * k / 10), base + height * 0.4)
             for k in range(10)] + [(x, y, base + height)]
    offset = len(trunk)
    crown_faces = [(offset + k, offset + (k + 1) % 10, offset + 10) for k in range(10)]
    crown_faces.append(tuple(offset + k for k in reversed(range(10))))
    return kit.mesh_object(bpy, "Pine %d" % index, trunk + crown, faces + crown_faces)


def _pine(bpy, index, position, height):
    """A leaning trunk with flat crown pads: still plain, but a tree with parts."""
    x, y = position
    base = top_height(x, y)
    lean = 0.12 + 0.03 * index
    vertices, faces = [], []
    sections = 6
    for section in range(sections):
        low, high = section / sections, (section + 1) / sections
        centre = (x + lean * height * (low + high) / 2, y, base + height * 0.8 * (low + high) / 2)
        part, part_faces = kit.cylinder_geometry(0.32 * (1.0 - 0.6 * low), height * 0.8 / sections * 1.05, 10,
                                                 centre=centre, offset=len(vertices))
        vertices += part
        faces += part_faces
    pads = ((0.0, 0.0, 0.98, 2.6), (1.5, 0.6, 0.86, 2.0), (-1.1, -0.7, 0.78, 1.7), (0.4, 1.3, 0.7, 1.4))
    for pad, (dx, dy, level, radius) in enumerate(pads):
        centre = (x + lean * height * level + dx * (1 + 0.1 * index), y + dy, base + height * level)
        part, part_faces = kit.uv_sphere_geometry(radius, 14, 7, centre=centre, offset=len(vertices),
                                                  scale=(1.0, 0.9, 0.32))
        vertices += part
        faces += part_faces
    return kit.mesh_object(bpy, "Pine %d" % index, vertices, faces, smooth=True)


def _blockout(bpy):
    scene = bpy.context.scene
    kit.mesh_object(bpy, "Island", *island_geometry(), smooth=True)
    pond = [(POND_CENTRE[0] + POND_SIZE[0] / 2 * 0.92 * math.cos(2 * math.pi * k / 32),
             POND_CENTRE[1] + POND_SIZE[1] / 2 * 0.92 * math.sin(2 * math.pi * k / 32), -0.3) for k in range(32)]
    kit.mesh_object(bpy, "Pond", pond, [tuple(range(32))])
    for index, (position, height) in enumerate(PINES, 1):
        _pine_stand_in(bpy, index, position, height)
    camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"], clip_end=20000.0)
    camera.data.sensor_width, camera.data.sensor_fit = 36.0, "HORIZONTAL"
    scene.camera = camera     # The new camera first, then the default one goes, as the prompt asks.
    kit.remove_default_objects(bpy)
    kit.sun_object(bpy, "Sun", SUN_ELEVATION, SUN_AZIMUTH, (1.0, 0.5, 0.32), 3.0, angle_deg=1.0)
    scene.world = kit.sky_world(bpy, "First Light Sky", SUN_ELEVATION, SUN_AZIMUTH, strength=1.0, aerosol=0.6)
    scene.render.engine = "CYCLES"
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1920, 1080, 100
    scene.view_settings.view_transform = "AgX"
    scene.cycles.use_denoising = True


def _rock_form(bpy):
    island = bpy.data.objects["Island"]
    subdivision = island.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels, subdivision.render_levels = 2, 3
    texture = bpy.data.textures.new("Island Erosion", "CLOUDS")
    texture.noise_scale, texture.noise_depth = 5.0, 3
    displace = island.modifiers.new("Erosion", "DISPLACE")
    displace.texture, displace.texture_coords = texture, "GLOBAL"
    displace.strength, displace.mid_level = 1.6, 0.5
    boulders = ((-15.0, 6.0, 1.1), (-4.0, 10.5, 0.8), (16.5, 5.0, 1.3), (11.0, -8.5, 0.9), (-2.0, -11.0, 0.7))
    for index, (x, y, radius) in enumerate(boulders, 1):
        kit.mesh_object(bpy, "Boulder %d" % index, *kit.rock_geometry(radius, seed=20 + index),
                        location=(x, y, top_height(x, y) + radius * 0.3), smooth=True)
    for index, (x, y, z, radius) in enumerate(((-6.0, 1.0, -21.0, 1.6), (9.0, -2.0, -24.5, 1.1), (1.0, 4.0, -26.0, 0.8)), 1):
        shard = kit.mesh_object(bpy, "Shard %d" % index, *kit.rock_geometry(radius, seed=40 + index, squash=1.9),
                                location=(x, y, z), smooth=True)
        shard.rotation_euler = (0.1 * index, 0.0, 0.8 * index)


def _rock_material(bpy):
    """A height-banded stone whose noise also displaces: displacement and bump, as the prompt asks."""
    material = kit.height_ramp_material(bpy, "Island Sandstone", [
        (KEEL_Z, (0.16, 0.15, 0.15)), (-14.0, (0.2, 0.18, 0.16)), (CLIFF_Z, (0.36, 0.3, 0.22)),
        (-3.0, (0.28, 0.22, 0.16)), (-1.0, (0.42, 0.36, 0.27)), (HIGH_GROUND, (0.12, 0.1, 0.08))], roughness=0.82)
    tree = material.node_tree
    output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value, noise.inputs["Detail"].default_value = 1.4, 8.0
    displacement = tree.nodes.new("ShaderNodeDisplacement")
    displacement.inputs["Scale"].default_value, displacement.inputs["Midlevel"].default_value = 0.25, 0.5
    tree.links.new(noise.outputs[0], displacement.inputs["Height"])
    tree.links.new(displacement.outputs["Displacement"], output.inputs["Displacement"])
    material.displacement_method = "BOTH"
    for obj in bpy.context.scene.objects:
        if obj.name == "Island" or obj.name.startswith(("Boulder", "Shard")):
            kit.assign(obj, material)


def _scatter_group(bpy, name, material, density, seed):
    """Geometry Nodes: points on the emitter's faces, a small cone on each (the emitter itself is not output)."""
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    source, target = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    points = nodes.new("GeometryNodeDistributePointsOnFaces")
    points.inputs["Density"].default_value, points.inputs["Seed"].default_value = density, seed
    blade = nodes.new("GeometryNodeMeshCone")
    blade.inputs["Vertices"].default_value = 5
    blade.inputs["Radius Bottom"].default_value, blade.inputs["Depth"].default_value = 0.07, 0.42
    paint = nodes.new("GeometryNodeSetMaterial")
    paint.inputs["Material"].default_value = material
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(source.outputs[0], points.inputs["Mesh"])
    links.new(blade.outputs["Mesh"], paint.inputs["Geometry"])
    links.new(points.outputs["Points"], instance.inputs["Points"])
    links.new(paint.outputs["Geometry"], instance.inputs["Instance"])
    links.new(instance.outputs["Instances"], target.inputs[0])
    return group


def _vegetation(bpy):
    turf = _material(bpy, "Grass Blades", (0.2, 0.24, 0.08), 0.7)
    rx, ry, segments = ISLAND_SIZE[0] / 2 * 0.86, ISLAND_SIZE[1] / 2 * 0.86, 48
    vertices = [(0.0, 0.0, top_height(0.0, 0.0) + 0.05)]
    for ring in (0.5, 1.0):
        for index in range(segments):
            theta = 2 * math.pi * index / segments
            x, y = rx * ring * _outline(theta) * math.cos(theta), ry * ring * _outline(theta) * math.sin(theta)
            vertices.append((x, y, top_height(x, y) + 0.05))
    faces = [(0, 1 + index, 1 + (index + 1) % segments) for index in range(segments)]
    faces += [(1 + index, 1 + segments + index, 1 + segments + (index + 1) % segments, 1 + (index + 1) % segments)
              for index in range(segments)]
    grass = kit.mesh_object(bpy, "Grass", vertices, faces)
    kit.assign(grass, turf)
    grass.modifiers.new("Grass Scatter", "NODES").node_group = _scatter_group(bpy, "Grass Scatter", turf, 10.0, 7)
    bark, needles = _material(bpy, "Pine Bark", (0.22, 0.11, 0.07), 0.9), _material(bpy, "Pine Needles",
                                                                                    (0.05, 0.1, 0.05), 0.6)
    for index, (position, height) in enumerate(PINES, 1):
        _replace(bpy, "Pine %d" % index)
        kit.assign(_pine(bpy, index, position, height), needles, bark)
    strands = []
    for index in range(7):
        theta = 2.4 + 0.5 * index
        x, y = 0.9 * ISLAND_SIZE[0] / 2 * math.cos(theta), 0.9 * ISLAND_SIZE[1] / 2 * math.sin(theta)
        length = 2.5 + 0.6 * (index % 3)
        strands.append(((0.12, 0.12, length), (x, y, CLIFF_Z - 1.0 - length / 2)))
    kit.assign(kit.mesh_object(bpy, "Roots", *kit.merged_boxes(strands)), bark)


def _water(bpy):
    pond = bpy.data.objects["Pond"]
    kit.assign(pond, kit.principled(bpy, "Pond Water", (0.01, 0.03, 0.035), roughness=0.03, specular=0.6))
    # The fall leaves the left-hand tip and drops clear of the rock, in profile from the hero camera.
    fall = kit.box_object(bpy, "Waterfall", (0.3, 2.0, FALL_HEIGHT), (NOTCH[0] - 1.6, NOTCH[1], -0.6 - FALL_HEIGHT / 2))
    kit.assign(fall, kit.principled(bpy, "Falling Water", (0.85, 0.88, 0.9), roughness=0.35))
    mist = kit.box_object(bpy, "Mist", (14.0, 9.0, 13.0), (NOTCH[0] - 5.0, NOTCH[1] - 1.0, -14.0))
    kit.assign(mist, _volume_material(bpy, "Mist Volume", 0.05, anisotropy=0.6))
    mist.display_type = "WIRE"


def _plane(bpy, name, size, centre):
    half_x, half_y = size[0] / 2, size[1] / 2
    return kit.mesh_object(bpy, name, [(-half_x, -half_y, 0.0), (half_x, -half_y, 0.0), (half_x, half_y, 0.0),
                                       (-half_x, half_y, 0.0)], [(0, 1, 2, 3)], location=centre)


def _atmosphere(bpy):
    cloud = _volume_material(bpy, "Cloud Volume", 0.03)
    deck = kit.box_object(bpy, "Cloud Deck", (2600.0, 2600.0, 50.0), (0.0, 1000.0, -88.0))
    kit.assign(deck, cloud)
    deck.display_type = "WIRE"
    surface = _material(bpy, "Cloud Surface", (0.85, 0.85, 0.86), 1.0)
    kit.assign(_plane(bpy, "Cloud Deck Far", (9000.0, 8500.0), (0.0, 6500.0, -90.0)), surface)
    kit.assign(_plane(bpy, "Cloud Deck Low", (10000.0, 10000.0), (0.0, 4000.0, -430.0)),
               _material(bpy, "Cloud Low", (0.6, 0.61, 0.65), 1.0))
    haze = _volume_material(bpy, "Haze Volume", 0.0005, color=(0.98, 0.95, 0.9), anisotropy=0.3)
    for name, size, centre in (("Horizon Haze", (14000.0, 8000.0, 175.0), (0.0, 8500.0, -40.0)),
                               ("Haze", (3200.0, 1100.0, 100.0), (0.0, 750.0, 10.0))):
        box = kit.box_object(bpy, name, size, centre)
        kit.assign(box, haze)
        box.display_type = "WIRE"
    for index, (x, y, z, size) in enumerate(((-38.0, 55.0, -22.0, (70.0, 26.0, 9.0)), (30.0, 90.0, -34.0, (95.0, 30.0, 11.0)),
                                             (-5.0, 40.0, -41.0, (60.0, 34.0, 8.0)), (52.0, 140.0, -12.0, (80.0, 24.0, 7.0))), 1):
        wisp = kit.box_object(bpy, "Cloud Wisp %d" % index, size, (x, y, z))
        kit.assign(wisp, cloud)
        wisp.display_type = "WIRE"
    stone = bpy.data.materials.get("Island Sandstone") or _material(bpy, "Islet Stone", (0.3, 0.26, 0.2))
    for index, (x, y, z, radius, squash) in enumerate(((-175.0, 520.0, -16.0, 14.0, 2.2), (240.0, 700.0, 6.0, 26.0, 0.5)), 1):
        islet = kit.mesh_object(bpy, "Islet %d" % index, *kit.rock_geometry(radius, seed=60 + index, squash=squash),
                                location=(x, y, z), smooth=True)
        kit.assign(islet, stone)
    wings, faces = [], []
    for index in range(12):
        x, y, z = -44.0 + 2.6 * (index % 4) + 0.7 * index, -6.0 + 1.9 * (index // 4), -6.0 + 1.1 * (index % 5)
        offset = len(wings)
        wings += [(x - 0.3, y, z + 0.08), (x, y + 0.07, z), (x + 0.3, y, z + 0.08), (x, y - 0.12, z)]
        faces += [(offset, offset + 1, offset + 3), (offset + 1, offset + 2, offset + 3)]
    kit.assign(kit.mesh_object(bpy, "Birds", wings, faces), _material(bpy, "Bird", (0.02, 0.02, 0.02), 0.8))


def _lighting(bpy):
    """A boost light on the island, and the final frame size: the look is the model's, the size is checked."""
    scene = bpy.context.scene
    sun = bpy.data.objects["Sun"]
    boost = bpy.data.objects.new("Sun Boost", bpy.data.lights.new("Sun Boost", "SUN"))
    boost.data.color, boost.data.energy, boost.data.angle = sun.data.color, 5.0, sun.data.angle
    boost.location, boost.rotation_euler = sun.location, sun.rotation_euler
    kit.link(bpy, boost)
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 3840, 2160, 100
    scene.view_settings.view_transform = "AgX"
    scene.cycles.use_denoising = True


def _final_frame(bpy):
    """Depth of field; the frame itself is rendered by the job the turn names (``TURNS[-1]["job"]``)."""
    camera = bpy.data.objects["Hero Camera"]
    dof = camera.data.dof
    dof.use_dof, dof.aperture_fstop = True, 2.8
    dof.focus_distance = math.dist(HERO["location"], (0.0, -ISLAND_SIZE[1] / 2, 0.0))


BUILDERS = {"blockout": _blockout, "rock_form": _rock_form, "rock_material": _rock_material,
            "vegetation": _vegetation, "water": _water, "atmosphere": _atmosphere, "lighting": _lighting,
            "final_frame": _final_frame}


def build_turn(bpy, turn_id):
    """Apply one turn of the ground truth to the current scene."""
    BUILDERS[turn_id](bpy)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    """The ground-truth scene up to and including turn ``upto`` (all turns by default)."""
    for turn in TURNS:
        build_turn(bpy, turn["id"])
        if turn["id"] == upto:
            break
