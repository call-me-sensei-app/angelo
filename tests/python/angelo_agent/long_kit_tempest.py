# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in of ``scenario_oneshot_tempest``: a ship in a bottle on a period desk in a dark study.

The plainest build that follows the brief: every stated size, place and height is exact (``long_kit_tempest_values``)
and everything else is a simple placeholder shape (boxes, cylinders, lofts). Pure data API through
``scenario_build_kit``; metres, Z up. It carries what the checks measure (a bottle of the stated profile, a sea that
fills it up to its surface, a volume for the clouds, a hull, decks and masts at the stated heights, yards named as
parts of their masts, interiors inside the hull, a whale under the surface) and nothing the brief does not ask for.
"""

import math

import long_kit_tempest_values as values
import scenario_build_kit as kit
from long_kits import combine  # noqa: F401  (the long stand-ins' shared joiner; long_kit_tempest_ship imports it here)

V = values.V
PALETTE = {
    "oak": ("Dark Oak", (0.13, 0.07, 0.035), 0.6), "walnut": ("Walnut", (0.2, 0.1, 0.05), 0.5),
    "plaster": ("Smoked Blue Plaster", (0.05, 0.08, 0.1), 0.9), "brass": ("Brass", (0.6, 0.42, 0.18), 0.4, 0.9),
    "leather": ("Bound Leather", (0.12, 0.16, 0.08), 0.8), "linen": ("Brown Linen", (0.15, 0.11, 0.07), 0.95),
    "parchment": ("Parchment", (0.6, 0.48, 0.3), 0.9), "glass": ("Bottle Glass", (0.85, 0.92, 0.9), 0.02),
    "pane": ("Moonlit Pane", (0.25, 0.4, 0.55), 0.1), "cork": ("Cork", (0.45, 0.28, 0.14), 0.95),
    "rope": ("Hemp Rope", (0.55, 0.45, 0.3), 0.95), "amber": ("Amber Glass", (0.8, 0.45, 0.12), 0.15),
    "sea": ("Storm Sea", (0.02, 0.12, 0.13), 0.15), "hull": ("Oak Planking", (0.3, 0.17, 0.08), 0.7),
    "teak": ("Teak Deck", (0.45, 0.3, 0.17), 0.7), "iron": ("Iron", (0.035, 0.035, 0.037), 0.5, 0.8),
    "sail": ("Stained Linen", (0.6, 0.58, 0.5), 0.9), "canvas": ("Canvas", (0.5, 0.47, 0.4), 0.9),
    "whale": ("Blue Grey Skin", (0.25, 0.32, 0.38), 0.5), "eye": ("Dark Eye", (0.01, 0.01, 0.01), 0.1),
    "ivory": ("Ivory Dial", (0.8, 0.76, 0.6), 0.6),
}


class _Materials:
    def __init__(self, bpy):
        self.bpy, self.made = bpy, {}

    def get(self, key):
        if key not in self.made:
            name, colour, roughness, *metal = PALETTE[key]
            material = kit.principled(self.bpy, name, colour, roughness=roughness, metallic=(metal or [0.0])[0],
                                      transmission=1.0 if key in ("glass", "amber") else 0.0)
            if key in ("glass", "amber", "pane"):   # Clear enough in the references to show what is behind.
                bsdf = next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
                bsdf.inputs["IOR"].default_value = 1.46
                bsdf.inputs["Alpha"].default_value = {"glass": 0.12, "amber": 0.5, "pane": 0.6}[key]
                if hasattr(material, "surface_render_method"):
                    material.surface_render_method = "BLENDED"
            self.made[key] = material
        return self.made[key]


def box(lo, hi):
    """An axis-aligned box from its low and high corners."""
    return kit.box_geometry([h - l for l, h in zip(lo, hi)], [(l + h) / 2 for l, h in zip(lo, hi)])


def cyl(radius, centre, depth, axis="Z", segments=16):
    return kit.cylinder_geometry(radius, depth, segments, centre, axis=axis)


def beam(a, b, r):
    """A square spar of half-width ``r`` from point ``a`` to point ``b`` (rigging lines, the bowsprit)."""
    d = [b[k] - a[k] for k in range(3)]
    length = math.sqrt(sum(c * c for c in d)) or 1.0
    d = [c / length for c in d]
    helper = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (d[1] * helper[2] - d[2] * helper[1], d[2] * helper[0] - d[0] * helper[2], d[0] * helper[1] - d[1] * helper[0])
    n = math.sqrt(sum(c * c for c in u))
    u = [c / n * r for c in u]
    w = [(d[1] * u[2] - d[2] * u[1]), (d[2] * u[0] - d[0] * u[2]), (d[0] * u[1] - d[1] * u[0])]
    corners = [(su, sw) for su, sw in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    vertices = [tuple(p[k] + su * u[k] + sw * w[k] for k in range(3)) for p in (a, b) for su, sw in corners]
    faces = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    return vertices, faces


def loft(sections, closed_ends=True):
    """Faces joining equal-length closed rings of points (each ring one cross-section), with end caps."""
    count = len(sections[0])
    vertices = [p for ring in sections for p in ring]
    faces = []
    for s in range(len(sections) - 1):
        for i in range(count):
            j = (i + 1) % count
            faces.append((s * count + i, s * count + j, (s + 1) * count + j, (s + 1) * count + i))
    if closed_ends:
        faces.append(tuple(reversed(range(count))))
        faces.append(tuple((len(sections) - 1) * count + i for i in range(count)))
    return vertices, faces


class Builder:
    def __init__(self, bpy):
        self.bpy, self.materials = bpy, _Materials(bpy)

    def add(self, name, geometry, key, smooth=False):
        obj = kit.mesh_object(self.bpy, name, *geometry, smooth=smooth)
        kit.assign(obj, self.materials.get(key))
        return obj

    # ------------------------------------------------------------ the study
    def study(self):
        hx, hy, h, t = V["floor_w"] / 2, V["floor_d"] / 2, V["wall_h"], V["wall_t"]
        self.add("Study Floor", box((-hx, -hy, -0.05), (hx, hy, 0.0)), "oak")
        wx0, wx1 = V["win_x"] - V["win_w"] / 2, V["win_x"] + V["win_w"] / 2
        north = [box((-hx - t / 2, hy - t / 2, 0), (wx0, hy + t / 2, h)), box((wx1, hy - t / 2, 0), (hx + t / 2, hy + t / 2, h)),
                 box((wx0, hy - t / 2, 0), (wx1, hy + t / 2, V["win_sill"])),
                 box((wx0, hy - t / 2, V["win_head"]), (wx1, hy + t / 2, h))]
        self.add("Study Wall North", combine(north), "plaster")
        self.add("Study Wall West", box((-hx - t / 2, -hy, 0), (-hx + t / 2, hy, h)), "plaster")
        self.add("Study Wall East", box((hx - t / 2, -hy, 0), (hx + t / 2, hy, h)), "plaster")
        face = hy - t / 2
        self.add("Study Wainscot", combine([box((-hx + t / 2, face - 0.02, 0), (wx0 - 0.01, face, V["wainscot_h"])),
                                            box((wx1 + 0.01, face - 0.02, 0), (hx - t / 2, face, V["wainscot_h"])),
                                            box((-hx + t / 2, -hy, 0), (-hx + t / 2 + 0.02, face, V["wainscot_h"])),
                                            box((hx - t / 2 - 0.02, -hy, 0), (hx - t / 2, face, V["wainscot_h"]))]), "oak")
        s, top = V["win_sill"], V["win_head"]
        frame = [box((wx0, hy - 0.03, s), (wx0 + 0.06, hy + 0.03, top)), box((wx1 - 0.06, hy - 0.03, s), (wx1, hy + 0.03, top)),
                 box((V["win_x"] - 0.03, hy - 0.03, s), (V["win_x"] + 0.03, hy + 0.03, top)),
                 box((wx0, hy - 0.2, s - 0.04), (wx1, hy + 0.03, s))]
        frame += [box((wx0, hy - 0.03, z - 0.03), (wx1, hy + 0.03, z + 0.03)) for z in (s + 0.55, s + 1.05)]
        self.add("Study Window", combine(frame), "walnut")
        self.add("Study Window Glass", box((wx0, hy - 0.005, s), (wx1, hy + 0.005, top)), "pane")
        self.add("Study Curtains", combine([box((wx0 - 0.45, face - 0.12, 0.02), (wx0 - 0.05, face - 0.04, 2.6)),
                                            box((wx1 + 0.05, face - 0.12, 0.02), (wx1 + 0.45, face - 0.04, 2.6))]), "linen")
        cx, cy, cw, cd, ch = V["case_x"], V["case_y"], V["case_w"], V["case_d"], V["case_h"]
        x0, x1, y0, y1 = cx - cw / 2, cx + cw / 2, cy - cd / 2, cy + cd / 2
        parts = [box((x, y0, 0), (x + 0.03, y1, ch - 0.08)) for x in (x0, cx - 0.015, x1 - 0.03)]
        shelves = [0.1 + i * 0.48 for i in range(5)]
        parts += [box((x0, y0, z - 0.02), (x1, y1, z)) for z in shelves]
        parts.append(box((x0, y0, ch - 0.08), (x1, y1, ch)))
        self.add("Study Bookcase", combine(parts), "walnut")
        books = [box((x0 + 0.04, y0 + 0.04, z), (x1 - 0.04, y1 - 0.03, z + V["book_low"] + 0.04)) for z in shelves]
        self.add("Study Atlases", combine(books), "leather")

    def desk(self):
        x, y, w, d, top = V["desk_x"], V["desk_y"], V["desk_w"], V["desk_d"], V["desk_top"]
        x0, x1, y0, y1 = x - w / 2, x + w / 2, y - d / 2, y + d / 2
        parts = [box((x0, y0, top - 0.04), (x1, y1, top)), box((x0 + 0.02, y0 + 0.03, 0), (x0 + 0.5, y1 - 0.02, top - 0.04)),
                 box((x1 - 0.5, y0 + 0.03, 0), (x1 - 0.02, y1 - 0.02, top - 0.04))]
        parts += [box((px - 0.04, y0 + 0.015, z), (px + 0.04, y0 + 0.03, z + 0.02))
                  for px in (x0 + 0.26, x1 - 0.26) for z in (0.15, 0.38, 0.6)]
        self.add("Study Desk", combine(parts), "oak")
        a = math.radians(V["chart_turn"])
        hw, hd = V["chart_w"] / 2, V["chart_d"] / 2
        ring = [(V["chart_x"] + px * math.cos(a) - py * math.sin(a), V["chart_y"] + px * math.sin(a) + py * math.cos(a))
                for px, py in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))]
        self.add("Study Chart", ([(p[0], p[1], top + 0.001) for p in ring] + [(p[0], p[1], top + 0.002) for p in ring],
                                 [(0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]),
                 "parchment")
        cx, cy = V["chart_x"] + 0.1, V["chart_y"] + 0.05
        self.add("Study Dividers", combine([beam((cx, cy, top + 0.006), (cx - 0.1, cy + 0.06, top + 0.003), 0.002),
                                            beam((cx, cy, top + 0.006), (cx - 0.05, cy - 0.1, top + 0.003), 0.002)]), "brass")
        r = V["compass_dia"] / 2
        self.add("Study Brass Compass", combine([cyl(r, (V["compass_x"], V["compass_y"], top + 0.003), 0.006, segments=24),
                                                 box((V["compass_x"] - 0.025, V["compass_y"] - 0.002, top + 0.006),
                                                     (V["compass_x"] + 0.025, V["compass_y"] + 0.002, top + 0.008))]), "brass")
        coins = []
        for i in range(12):
            px = V["coins_x0"] + 0.02 + (i * 0.37 % 1.0) * (V["coins_x1"] - V["coins_x0"] - 0.04)
            py = V["coins_y0"] + 0.02 + (i * 0.61 % 1.0) * (V["coins_y1"] - V["coins_y0"] - 0.04)
            coins.append(cyl(0.0075 + 0.0075 * (i % 3) / 2, (px, py, top + 0.0015), 0.003, segments=12))
        self.add("Study Coins", combine(coins), "brass")
        lx, ly, lh, foot = V["lamp_x"], V["lamp_y"], V["lamp_h"], V["lamp_foot"]
        lamp = [cyl(foot / 2, (lx, ly, top + 0.015), 0.03, segments=6), cyl(foot / 2 - 0.015, (lx, ly, top + 0.12), 0.18,
                                                                              segments=6),
                cyl(0.04, (lx, ly, top + 0.235), 0.05, segments=6), cyl(0.02, (lx, ly, top + lh - 0.01), 0.02, segments=8)]
        self.add("Study Lantern", combine(lamp), "brass")
        self.add("Study Lantern Panes", cyl(foot / 2 - 0.02, (lx, ly, top + 0.12), 0.17, segments=6), "amber")
        cx, cy, chh = V["candle_x"], V["candle_y"], V["candle_h"]
        self.add("Study Candle Lantern", combine([cyl(0.04, (cx, cy, top + 0.01), 0.02, segments=6),
                                                  cyl(0.032, (cx, cy, top + 0.07), 0.1, segments=6),
                                                  cyl(0.02, (cx, cy, top + chh - 0.02), 0.04, segments=6)]), "brass")

    # ------------------------------------------------------------ the stand and the bottle
    def stand(self):
        y, z, top, desk = V["axis_y"], V["axis_z"], V["plinth_top"], V["desk_top"]
        hw, hd = V["plinth_w"] / 2, V["plinth_d"] / 2
        parts = [box((-hw, y - hd, desk + 0.006), (hw, y + hd, desk + 0.018)),
                 box((-hw + 0.006, y - hd + 0.006, desk + 0.018), (hw - 0.006, y + hd - 0.006, top - 0.006)),
                 box((-hw + 0.003, y - hd + 0.003, top - 0.006), (hw - 0.003, y + hd - 0.003, top))]
        parts += [cyl(0.012, (sx * (hw - 0.03), y + sy * (hd - 0.03), desk + 0.003), 0.006, segments=10)
                  for sx in (-1, 1) for sy in (-1, 1)]
        self.add("Bottle Plinth", combine(parts), "walnut")
        r = V["body_dia"] / 2
        for name, x in (("Bottle Cradle West", V["cradle_wx"]), ("Bottle Cradle East", V["cradle_ex"])):
            half, horn = V["cradle_d"] / 2, V["cradle_horn"]
            reach = math.sqrt(r * r - (z - horn) ** 2)
            ring = [(y - half, top), (y + half, top), (y + half, horn)]
            ring += [(y + reach * math.cos(math.pi * k / 16), z - math.sqrt(max(0.0, r * r - (reach * math.cos(math.pi * k / 16)) ** 2)))
                     for k in range(17)]
            ring += [(y - half, horn)]
            sections = [[(x + dx, py, pz) for py, pz in ring] for dx in (-V["cradle_t"] / 2, V["cradle_t"] / 2)]
            finials = [kit.uv_sphere_geometry(V["finial"] / 2, 10, 6, (x, y + s * (half - 0.0025), horn + V["finial"] / 2))
                       for s in (-1, 1)]
            self.add(name, combine([loft(sections)] + finials), "walnut")
        rings = []
        for px, pr in values.PROFILE:
            rings.append([(px, y + max(pr, 1e-4) * math.cos(2 * math.pi * k / 32),
                           z + max(pr, 1e-4) * math.sin(2 * math.pi * k / 32)) for k in range(32)])
        vertices, faces = loft(rings, closed_ends=False)
        self.add("Bottle Glass", (vertices, faces), "glass", smooth=True)
        self.add("Bottle Cork", cyl(V["cork_out"] / 2, ((V["cork_x0"] + V["cork_x1"]) / 2, y, z), V["cork_x1"] - V["cork_x0"],
                                    "X", 24), "cork")
        self.add("Bottle Neck Rope", cyl(0.055, ((V["rope_x0"] + V["rope_x1"]) / 2, y, z), V["rope_x1"] - V["rope_x0"],
                                         "X", 24), "rope")
        knot = (V["knot_x"], V["knot_y"], V["knot_z"])
        self.add("Bottle Knot", kit.uv_sphere_geometry(0.009, 12, 6, knot), "rope")
        c = (V["hc_x"], V["hc_y"], V["hc_z"])
        self.add("Bottle Compass", combine([cyl(V["hc_dia"] / 2, c, 0.006, "Y", 24),
                                            box((c[0] - 0.003, c[1] - 0.002, c[2] + 0.025), (c[0] + 0.003, c[1] + 0.002, c[2] + 0.031))]),
                 "brass")
        lc = (V["hl_x"], V["hl_y"], V["hl_z"])
        self.add("Bottle Lantern", combine([cyl(0.02, (lc[0], lc[1], lc[2] - 0.0235), 0.003, segments=6),
                                            cyl(0.02, (lc[0], lc[1], lc[2] + 0.0185), 0.003, segments=6),
                                            cyl(0.008, (lc[0], lc[1], lc[2] + 0.0225), 0.005, segments=8)]), "brass")
        self.add("Bottle Lantern Panes", cyl(0.018, lc, 0.04, segments=6), "amber")
        self.add("Bottle Suspension Lines", combine([beam(knot, (c[0], c[1], c[2] + 0.031), 0.0008),
                                                     beam(knot, (lc[0], lc[1], lc[2] + 0.025), 0.0008)]), "rope")
        px, pz, pw, ph = V["plaque_x"], V["plaque_z"], V["plaque_w"], V["plaque_h"]
        front = y - hd + 0.006
        self.add("Bottle Plaque", box((px - pw / 2, front - 0.003, pz - ph / 2), (px + pw / 2, front, pz + ph / 2)), "brass")

    def inside(self):
        y, z, sea, wall = V["axis_y"], V["axis_z"], V["sea_z"], V["glass_wall"]
        sections = []
        x = V["base_x"] + 0.001
        while x < V["lip_x"]:
            ri = values.radius_at(x) - wall
            if ri > z - sea + 0.001:
                start = math.asin((sea - z) / ri)
                arc = [start - (math.pi + 2 * start) * k / 20 for k in range(21)]   # Low half, chord on top.
                sections.append([(x, y + ri * math.cos(t), z + ri * math.sin(t)) for t in arc])
            x += 0.005
        self.add("Bottle Sea", loft(sections), "sea")
        clouds = kit.mesh_object(self.bpy, "Bottle Storm Clouds", *box((-0.2, y - 0.06, V["cloud_base"]), (0.15, y + 0.06, 1.1)))
        material = self.bpy.data.materials.new("Storm Cloud Volume")
        material.use_nodes = True
        tree = material.node_tree
        for node in list(tree.nodes):
            if node.type == "BSDF_PRINCIPLED":
                tree.nodes.remove(node)
        volume = tree.nodes.new("ShaderNodeVolumePrincipled")
        volume.inputs["Density"].default_value = 30.0
        volume.inputs["Color"].default_value = (0.35, 0.38, 0.42, 1.0)
        output = next(n for n in tree.nodes if n.type == "OUTPUT_MATERIAL")
        tree.links.new(volume.outputs["Volume"], output.inputs["Volume"])
        kit.assign(clouds, material)
        moon = self.bpy.data.materials.new("Moon Glow")
        moon.use_nodes = True
        bsdf = next(n for n in moon.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Emission Color"].default_value = (0.6, 0.8, 1.0, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 4.0
        obj = kit.mesh_object(self.bpy, "Bottle Moon", *kit.uv_sphere_geometry(V["moon_dia"] / 2, 12, 6,
                                                                           (V["moon_x"], V["moon_y"], V["moon_z"])))
        kit.assign(obj, moon)


def build(bpy):
    import long_kit_tempest_ship as ship
    builder = Builder(bpy)
    builder.study()
    builder.desk()
    builder.stand()
    builder.inside()
    ship.build_ship(builder)
    ship.build_below(builder)
    ship.build_whale(builder)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Study Moonlight", 45.0, 135.0, (0.55, 0.7, 0.9), 3.0, angle_deg=3.0)
    kit.point_light(bpy, "Study Lantern Light", (V["lamp_x"], V["lamp_y"], V["desk_top"] + 0.12), (1.0, 0.65, 0.3), 15.0, 0.02)
    kit.point_light(bpy, "Bottle Glow", (0.0, V["axis_y"] - 0.05, 1.06), (0.6, 0.8, 1.0), 2.0, 0.005)
    kit.point_light(bpy, "Study Fill", (0.4, 0.0, 1.6), (1.0, 0.85, 0.7), 150.0, 0.5)
    scene.world = kit.colour_world(bpy, "Night Study", (0.03, 0.036, 0.048), strength=1.0)
    if hasattr(scene.eevee, "use_raytracing"):
        scene.eevee.use_raytracing = True
    scene.camera = kit.camera_object(bpy, "Hero Camera", (V["cam_x"], V["cam_y"], V["cam_z"]),
                                     (V["look_x"], V["look_y"], V["look_z"]), float(V["lens"]))
    scene.camera.data.clip_start = 0.01
