# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The website files of ``bench_native_reads --web``: every path's numbers, and the scene as a point cloud.

``native-reads.json`` lists every native read that has a stock-Blender
equivalent, the small and losing ones included (and, under ``retired``, reads
compared once and retired since, with their last numbers): per path the fastest
supported stock route (``stock_route``), every other stock route with its
time, Angelo's route, medians and ranges in the path's unit, and the main
thread's instructions where the platform counts them. ``castle-points.bin``
is the scene's vertices in world space for the read animation, and
``castle-points.json`` says how it was made. ``castle-only-points.bin`` is
denser and holds the Castle collection only, in the order the reads visit
it, with each object's point range in ``castle-only-points.json``. To write
only that from a saved scene, without timing anything::

    BLENDER -b SCENE.blend --python tests/python/angelo_agent/bench_native_reads_web.py -- OUT_DIR
"""

import json
import math
from pathlib import Path

COLLECTIONS = ("Terrain", "Castle", "Forest", "Rocks", "Village", "Props")


def _scale(entry, value):
    """Milliseconds in the path's unit: per million vertices for a per-vertex path, else per check."""
    if value is None:
        return None
    return round(value / (entry["vertices"] / 1e6), 3) if entry.get("vertices") else value


def _row(entry):
    timed = [route for route in entry["routes"] if "median_ms" in route]
    stock = [route for route in timed if route["side"] == "stock"]
    supported = [route for route in stock if route["supported"]]
    best = min(supported or stock, key=lambda route: route["median_ms"]) if stock else None
    angelo = next((route for route in timed if route["side"] == "angelo"), None)
    count = lambda route: None if route is None or route.get("instructions") is None else int(round(_scale(
        entry, route["instructions"])))
    row = {"id": entry["id"], "label": entry["label"], "unit": entry["unit"], "per_vertex": entry["per_vertex"],
           "stock_route": best and best["route"],
           "stock_other_routes": [{"route": route["route"], "ms": _scale(entry, route["median_ms"]),
                                   "supported": route["supported"]} for route in stock if route is not best],
           "stock_ms": best and _scale(entry, best["median_ms"]),
           "stock_range": best and [_scale(entry, value) for value in best["range_ms"]],
           "angelo_route": angelo and angelo["route"], "angelo_ms": angelo and _scale(entry, angelo["median_ms"]),
           "angelo_range": angelo and [_scale(entry, value) for value in angelo["range_ms"]],
           "instructions": ({"stock": count(best), "angelo": count(angelo)}
                            if count(best) is not None and count(angelo) is not None else None),
           "parity": all(route.get("parity", False) for route in timed),
           "note": entry.get("note") or ""}
    errors = [{"route": route["route"], "error": route["error"]} for route in entry["routes"] if "error" in route]
    if "error" in (entry.get("product_limits") or {}):
        errors.append({"route": "the same scan with the product's own limits",
                       "error": entry["product_limits"]["error"]})
    if errors:
        row["not_timed"] = errors
    return row


def points(path, per_object=False):
    """Every n-th vertex of every mesh object, world space, collection by collection; returns (count, facts)."""
    import bpy
    import numpy
    scene = bpy.context.scene
    objects = [obj for name in COLLECTIONS if bpy.data.collections.get(name)
               for obj in bpy.data.collections[name].objects if obj.type == "MESH"]
    total = sum(len(obj.data.vertices) for obj in objects)
    stride = max(1, math.ceil(total / 300000))
    chunks, ranges, offset = [], [], 0
    for collection in COLLECTIONS:
        start = sum(len(chunk) for chunk in chunks)
        for obj in (o for o in objects if o.users_collection[0].name == collection):
            local = numpy.empty(len(obj.data.vertices) * 3, numpy.float32)
            obj.data.attributes["position"].data.foreach_get("vector", local)
            picked = local.reshape(-1, 3)[(-offset) % stride::stride].astype(numpy.float64)
            offset += len(obj.data.vertices)
            matrix = numpy.array(obj.matrix_world, dtype=numpy.float64)
            chunks.append((picked @ matrix[:3, :3].T + matrix[:3, 3]).astype("<f4"))
        ranges.append({"collection": collection, "first": start, "count": sum(len(c) for c in chunks) - start})
    cloud = numpy.concatenate(chunks)
    Path(path).write_bytes(cloud.tobytes())
    return {"file": Path(path).name, "format": "little-endian float32 x, y, z per point (12 bytes)",
            "points": int(len(cloud)), "true_vertices": int(total), "every_nth_vertex": stride,
            "order": "collections %s; objects in collection order; each object's vertices in index order "
                     "(the order the reads walk them)" % ", ".join(COLLECTIONS),
            "bounds": {"min": [round(float(v), 3) for v in cloud.min(axis=0)],
                       "max": [round(float(v), 3) for v in cloud.max(axis=0)]},
            "collections": ranges, "objects": len(objects), "scene": scene.name}


def castle_points(path, every=3, collection="Castle"):
    """Every ``every``-th vertex of the collection's mesh objects, world space, in the reads' order: the scene's
    objects in order (``bench_native_reads.prepare``), each object's vertices in index order. A mesh several
    objects share is read once, at its first user; its later users are kept, marked ``read: false``."""
    import bpy
    import numpy
    members = set(bpy.data.collections[collection].all_objects)
    objects = [obj for obj in bpy.context.scene.objects if obj in members and obj.type == "MESH"]
    chunks, rows, seen, offset, count = [], [], set(), 0, 0
    low, high = numpy.full(3, numpy.inf), numpy.full(3, -numpy.inf)
    for obj in objects:
        local = numpy.empty(len(obj.data.vertices) * 3, numpy.float32)
        obj.data.attributes["position"].data.foreach_get("vector", local)
        matrix = numpy.array(obj.matrix_world, dtype=numpy.float64)
        world = local.reshape(-1, 3).astype(numpy.float64) @ matrix[:3, :3].T + matrix[:3, 3]
        low, high = numpy.minimum(low, world.min(axis=0)), numpy.maximum(high, world.max(axis=0))
        picked = world[(-offset) % every::every]
        offset += len(obj.data.vertices)
        chunks.append(picked.astype("<f4"))
        rows.append({"name": obj.name, "first": count, "count": len(picked), "vertices": len(obj.data.vertices),
                     "read": obj.data.name not in seen})
        seen.add(obj.data.name)
        count += len(picked)
    cloud = numpy.concatenate(chunks)
    Path(path).write_bytes(cloud.tobytes())
    return {"file": Path(path).name, "format": "little-endian float32 x, y, z per point (12 bytes)",
            "points": int(len(cloud)), "true_vertices": int(offset), "every_nth_vertex": every,
            "collection": collection,
            "order": "the scene's objects in the order the reads visit them, each object's vertices in index "
                     "order; every n-th vertex of the whole stream",
            "bounds": {"min": [round(float(v), 3) for v in cloud.min(axis=0)],
                       "max": [round(float(v), 3) for v in cloud.max(axis=0)]},
            "true_bounds": {"min": [round(float(v), 3) for v in low], "max": [round(float(v), 3) for v in high]},
            "objects": rows}


def web_json(report):
    """``native-reads.json`` of a full report (``native-reads-full.json``)."""
    rows = [_row(entry) for entry in report["paths"]]
    check = report.get("per_edit_check") or {}
    wall = check.get("Castle Wall North") or {}
    web = {"machine": "%s, %s cores, %s" % (report["machine"]["cpu"], report["machine"]["cores"],
                                            report["machine"]["platform"]),
           "blender": report["blender"], "angelo_commit": report["angelo_commit"],
           "harness_commit": report.get("harness_commit"),
           "scene": {"objects": report["scene"]["objects"], "vertices": report["scene"]["vertices"],
                     "label": "The dense castle scene of scenario-heavy (seeded): terrain, castle, forest, village; "
                              "%s unique mesh vertices" % format(report["scene"]["vertices"], ",")},
           "runs": report["runs"], "paths": rows, "retired": list(report.get("retired", ())),
           "per_edit_check": {"stock_ms": wall.get("stock_ms"), "angelo_ms": wall.get("angelo_ms"),
                              "what": "the whole safety check after one edit on this scene (the normals fix on "
                                      "'Castle Wall North', %s vertices with a vertex group): the edited object's "
                                      "exact revision and fingerprint, then the check that nothing else in the "
                                      "document changed" % format(wall.get("vertices", 0), ","),
                              "objects": {name: {"stock_ms": value["stock_ms"], "angelo_ms": value["angelo_ms"],
                                                 "vertices": value["vertices"]} for name, value in check.items()}}}
    return web


def write(folder, report, setup):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    web = web_json(report)
    rows = web["paths"]
    cloud = points(folder / "castle-points.bin")
    cloud["read_ms_per_million_vertices"] = {row["id"]: {"stock": row["stock_ms"], "angelo": row["angelo_ms"],
                                                         "stock_route": row["stock_route"],
                                                         "angelo_route": row["angelo_route"]}
                                             for row in rows if row["per_vertex"]}
    (folder / "castle-points.json").write_text(json.dumps(cloud, indent=1))
    castle = castle_points(folder / "castle-only-points.bin")
    (folder / "castle-only-points.json").write_text(json.dumps(castle, indent=1))
    (folder / "native-reads.json").write_text(json.dumps(web, indent=1))
    return web


if __name__ == "__main__":   # Blender -b SCENE.blend --python bench_native_reads_web.py -- OUT_DIR
    import sys
    out = Path(sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "native-reads-web")
    out.mkdir(parents=True, exist_ok=True)
    facts = castle_points(out / "castle-only-points.bin")
    (out / "castle-only-points.json").write_text(json.dumps(facts, indent=1))
    print("CASTLE_ONLY_POINTS", json.dumps({key: facts[key] for key in ("points", "true_vertices", "bounds")}))
