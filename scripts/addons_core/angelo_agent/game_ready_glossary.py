# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What each game-ready fact measured, in one sentence per kind of fact (``game_ready_metrics.glossary``, which
gives the settings' defaults and imports this when it is called). No Blender dependency.

The agent reads the check's numbers without seeing how they were made, so each result says it; only for the kinds
of fact that result holds (``held``): a check of the mesh alone has no Hausdorff to explain, and the whole text was
about 2.9 KB of its 3.7 KB (bench_result_brief_bytes.py; doc/angelo/game_ready_check.rst, "What each fact
measures").
"""

# The result keys each glossary entry explains, where they are not the entry's own name: a collider's
# ``hull_distance`` is a block of Hausdorff facts.
EXPLAINS = {"hausdorff": ("hausdorff", "hull_distance")}
# Blocks that hold no facts: the time per phase (``native_ms``) is named by phase ("silhouette", "collider"), and the
# glossary itself.
NOT_FACTS = frozenset({"native_ms", "measured"})


def held(result):
    """The keys of every block in ``result`` whose value is a fact; a string there is a note of what was not
    measured (the source's ``uv`` of a mesh without a UV map), so its key is not one."""
    found, pending = set(), [result]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            items = [(key, item) for key, item in value.items() if key not in NOT_FACTS]
            found.update(key for key, item in items if not isinstance(item, str))
            pending.extend(item for _key, item in items)
        elif isinstance(value, list):
            pending.extend(value)
    return found


def glossary(resolution, fov_degrees, texture_size, samples, silhouette_resolution, uv_resolution, facts=None):
    """Every entry, or with ``facts`` (the result it goes in) only those for the facts that result holds."""
    entries = _glossary(resolution, fov_degrees, texture_size, samples, silhouette_resolution, uv_resolution)
    if facts is None:
        return entries
    present = held(facts)
    return {name: text for name, text in entries.items() if present.intersection(EXPLAINS.get(name, (name,)))}


def _glossary(resolution, fov_degrees, texture_size, samples, silhouette_resolution, uv_resolution):
    return {
        "hausdorff": ("nearest-surface distance from every vertex plus %d area-weighted points of the LOD to the "
                      "source, and of the source to the LOD (points: both counts); max_m is the largest sampled, a "
                      "lower bound of the true Hausdorff distance; mean_m is over the area-weighted points both ways; "
                      "max_of_bounds divides by the source's bounding-box diagonal" % samples),
        "silhouette": ("covered pixels shared over covered by either, in 7 orthographic views (the 3 axes and the 4 "
                       "cube diagonals; a view and its opposite see the same outline) at %d px framing the source's "
                       "bounding sphere, or both meshes' when the LOD reaches outside it, so nothing is clipped; "
                       "worst_view is the direction looked along" % silhouette_resolution),
        "pixel_error": ("the Hausdorff max in pixels at each switch distance on a %d px tall view with a %g degree "
                        "vertical field of view; distance_at_1px_m is where it shrinks to one pixel"
                        % (resolution, fov_degrees)),
        "normals": ("degrees between the shading normal at each sample point of the source and of this LOD and the "
                    "other mesh's at its nearest point, both interpolated from corner normals"),
        "uv_shift": ("UV-space distance between the UV at each sample point of the source and of this LOD and the "
                     "other mesh's at its nearest point; points next to a seam may read the other side's UVs, so "
                     "trust p95 over max"),
        "skin_weights": ("half the summed absolute difference of the deforming groups' weights at each sample point "
                         "of the source and of this LOD and the other mesh's nearest point: 0 the same influences, 1 "
                         "none shared"),
        "uv": ("seam: 3D length and count of edges whose sides have different UVs; stretch: larger over smaller "
               "singular value of each triangle's surface-to-UV map (1 is none), area-weighted; texel_density: "
               "texels per meter at %d px, texture_size x sqrt(UV area / surface area), area-weighted, spread its "
               "coefficient of variation; overlap_of_used_area: pixel centres under two or more UV triangles over "
               "those under any, %d px over the UV bounds; mirrored_tris: clockwise UVs; collapsed_tris: zero UV "
               "area" % (texture_size, uv_resolution)),
        "topology": "edges with one triangle (open), with three or more (non-manifold), and zero-area triangles",
        "collider": ("closed and convex as tested (each face either way round); consistent_winding: every shared edge "
                     "walked both ways; volume over the source's convex hull (excess_over_hull); hull_distance: the "
                     "Hausdorff facts from the hull to the collider; mesh_outside: share of the source's points "
                     "(points) outside the collider by inside_test, and the farthest of them"),
        "meshopt_error": "meshoptimizer's own quadric error estimate, relative to the mesh extent",
        "lod0_vertex_buffer": ("triangle corners welded where the Blender vertex, UV, shading normal and material all "
                               "match: the vertex buffer an engine receives, which every meshopt LOD indexes"),
        "apart": ("the LOD's bounding box does not overlap the source's: facts compare the objects where they are, so "
                  "a LOD placed beside its source reads far from it"),
    }
