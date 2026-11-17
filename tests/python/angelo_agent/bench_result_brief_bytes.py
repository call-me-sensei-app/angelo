# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bytes two payloads put in front of a model: an ``angelo_game_ready_check`` result and a parallel worker's first
message. Host Python, deterministic, no Blender, provider or billing::

    python3 -B tests/python/angelo_agent/bench_result_brief_bytes.py [OUT.json]

**Game-ready results.** The real ``game_ready_host.check`` runs on parametric meshes (a UV sphere, a torus, an open
grid, a skinned capsule) with stand-ins for what needs Blender: the objects are given as read geometry, a reduction at
ratio r is the same surface tessellated with about r of its triangles (both methods; the result has the same keys and
number formats as a real one, not the same values), the convex hull is the source itself, and the kernels are
vectorized NumPy with the native ones' contract. Each result is then the copy a model reads
(``model_results.model_result``) serialized as each route sends it: Codex (``providers.tool_content``: compact,
ASCII-escaped) and Claude (``claude_direct``: compact, unescaped; the typed plan's continuation escapes as Codex).
Reported: the whole result's bytes and those of its ``measured`` member (``,"measured":{...}``).

**Worker briefs.** ``worker_request.joined`` builds each worker's first message for several histories of the
person's own messages (one short message; a long brief with two replies; a history past ``MAX_CHARS``) and worker
counts (1, 2, 4, 8, 16), with and without a 10.8 KB stand-in for the looked-up schemas (``worker_schemas.block``; the
size of the 2026-10-25 dev check's lead set). Per-worker parts are generic and differ in length. Reported per case:
each first message's bytes, the prefix every worker's first message shares (what a prompt cache keyed on a shared
prefix can serve to workers 2..N), and the bytes the batch sends that no earlier worker's message already began with.
An order with the shared blocks first cut those bytes 3.5 to 11 times here but saved no tokens live (doc/angelo/
agent_integration.rst, the worker briefing's paragraph: concurrent first requests, and no hit for a later worker),
so the product keeps the part first.

Nothing here is tuned to a scene or a benchmark; the inputs are listed in the report. Tokens are not counted (no
offline tokenizer ships with Angelo's Python).
"""

import json
import math
import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
for _name in ("bpy", "bmesh"):   # game_ready_host imports both; nothing here calls into them.
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["bmesh"].ops = types.SimpleNamespace()
from angelo_agent import game_ready_host as H, game_ready_mesh as M, game_ready_metrics as G  # noqa: E402
from angelo_agent import model_results, worker_request  # noqa: E402
from angelo_agent.game_ready_spec import CHECK, validate_game_ready  # noqa: E402


# Kernels -------------------------------------------------------------------------------------------------------------

def _closest(points, a, b, c):
    """Closest points of each point (P, 1, 3) on each triangle (T, 3) (Ericson 5.1.5), broadcast to (P, T, 3)."""
    ab, ac, ap = b - a, c - a, points - a
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp = points - b
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    cp = points - c
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    vc, vb, va = d1 * d4 - d3 * d2, d5 * d2 - d1 * d6, d3 * d6 - d5 * d4
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = 1.0 / (va + vb + vc)
        v, w = vb * denom, vc * denom
        out = a + ab * v[..., None] + ac * w[..., None]
        out = np.where(((vc <= 0) & (d1 >= 0) & (d3 <= 0))[..., None], a + ab * (d1 / (d1 - d3))[..., None], out)
        out = np.where(((vb <= 0) & (d2 >= 0) & (d6 <= 0))[..., None], a + ac * (d2 / (d2 - d6))[..., None], out)
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        out = np.where(((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0))[..., None], b + (c - b) * t[..., None], out)
    out = np.where(((d1 <= 0) & (d2 <= 0))[..., None], a, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[..., None], b, out)
    return np.where(((d6 >= 0) & (d5 <= d6))[..., None], c, out)


class Kernels:
    """``_bpy._angelo_reduce``'s kernel contract in NumPy: exact nearest points, pixel-centre coverage."""

    MAX_ATTRIBUTES = 32

    def status(self):
        return {"available": True}

    def surface(self, positions, tris):
        return np.asarray(positions, np.float64).reshape(-1, 3), np.asarray(tris, np.int64).reshape(-1, 3)

    def nearest(self, surface, points, chunk=96):
        positions, tris = surface
        points = np.asarray(points, np.float64).reshape(-1, 3)
        a, b, c = (positions[tris[:, k]][None] for k in range(3))
        distances = np.empty(len(points), np.float32)
        index = np.empty(len(points), np.int32)
        closest = np.empty((len(points), 3), np.float32)
        for start in range(0, len(points), chunk):
            p = points[start:start + chunk, None]
            q = _closest(p, a, b, c)
            d = np.linalg.norm(q - p, axis=-1)
            k = np.argmin(d, axis=1)
            rows = np.arange(len(k))
            distances[start:start + chunk], index[start:start + chunk] = d[rows, k], k
            closest[start:start + chunk] = q[rows, k]
        return distances.tobytes(), index.tobytes(), closest.tobytes()

    def rasterize(self, positions, tris, views, resolution):
        positions = np.asarray(positions, np.float64).reshape(-1, 3)
        tris = np.asarray(tris, np.int64).reshape(-1, 3)
        views = np.asarray(views, np.float64).reshape(-1, 8)
        out = np.zeros((len(views), resolution, resolution), np.int32)
        for v, row in enumerate(views):
            xy = np.stack([positions @ row[0:3] + row[3], positions @ row[4:7] + row[7]], 1)
            for tri in tris:
                a, b, c = xy[tri]
                area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if area == 0:
                    continue
                if area < 0:
                    b, c = c, b
                low = np.maximum(np.floor(np.minimum(np.minimum(a, b), c) - 0.5).astype(int), 0)
                high = np.minimum(np.ceil(np.maximum(np.maximum(a, b), c) - 0.5).astype(int), resolution - 1)
                if (high < low).any():
                    continue
                xs, ys = np.meshgrid(np.arange(low[0], high[0] + 1) + 0.5, np.arange(low[1], high[1] + 1) + 0.5)
                inside = np.ones(xs.shape, bool)
                for p, q in ((a, b), (b, c), (c, a)):
                    e = (q[0] - p[0]) * (ys - p[1]) - (q[1] - p[1]) * (xs - p[0])
                    dy, dx = q[1] - p[1], q[0] - p[0]
                    inside &= (e > 0) | ((e == 0) & (dy < 0 or (dy == 0 and dx < 0)))
                out[v, low[1]:high[1] + 1, low[0]:high[0] + 1] += inside
        return np.minimum(out, 255).astype(np.uint8).tobytes()


K = Kernels()


# Meshes ---------------------------------------------------------------------------------------------------------------

def surface_mesh(kind, nu, nv, scale=1.0, offset=(0.0, 0.0, 0.0), groups=None):
    """A parametric surface as read geometry: positions shared across its UV seam (a seam per wrapped edge), corner
    UVs from the parameters, corner normals from the shape. ``groups``: names of height bands it is weighted to."""
    u = np.arange(nu + 1) / nu
    v = np.arange(nv + 1) / nv
    uu, vv = np.meshgrid(u, v)
    if kind == "sphere":
        theta, phi = uu * 2 * math.pi, vv * math.pi
        p = np.stack([np.sin(phi) * np.cos(theta), np.sin(phi) * np.sin(theta), -np.cos(phi)], -1)
        n = p.copy()
    elif kind == "capsule":
        theta, phi = uu * 2 * math.pi, vv * math.pi
        p = np.stack([0.3 * np.sin(phi) * np.cos(theta), 0.3 * np.sin(phi) * np.sin(theta), -np.cos(phi)], -1)
        n = p / np.array([0.09, 0.09, 1.0])
    elif kind == "torus":
        theta, phi = uu * 2 * math.pi, vv * 2 * math.pi
        ring = 1.0 + 0.35 * np.cos(phi)
        p = np.stack([ring * np.cos(theta), ring * np.sin(theta), 0.35 * np.sin(phi)], -1)
        n = np.stack([np.cos(phi) * np.cos(theta), np.cos(phi) * np.sin(theta), np.sin(phi)], -1)
    elif kind == "grid":
        p = np.stack([uu * 2 - 1, vv * 2 - 1, 0.1 * np.sin(uu * 6) * np.cos(vv * 5)], -1)
        n = np.stack([np.zeros_like(uu), np.zeros_like(uu), np.ones_like(uu)], -1)
    else:
        raise ValueError(kind)
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    wrap_u = kind in ("sphere", "capsule", "torus")
    wrap_v = kind == "torus"
    ids = np.arange((nu + 1) * (nv + 1)).reshape(nv + 1, nu + 1)
    if wrap_u:
        ids[:, nu] = ids[:, 0]
    if wrap_v:
        ids[nv, :] = ids[0, :]
    if kind in ("sphere", "capsule"):   # One vertex per pole.
        ids[0, :], ids[nv, :] = ids[0, 0], ids[nv, 0]
    positions = (p.reshape(-1, 3) * scale + np.asarray(offset)).astype(np.float32)
    normals, uvs = n.reshape(-1, 3), np.stack([uu, vv], -1).reshape(-1, 2)
    tris, corners = [], []
    for j in range(nv):
        for i in range(nu):
            a, b, c, d = j * (nu + 1) + i, j * (nu + 1) + i + 1, (j + 1) * (nu + 1) + i + 1, (j + 1) * (nu + 1) + i
            for tri in ((a, b, c), (a, c, d)):
                shared = ids.reshape(-1)[list(tri)]
                if len(set(shared.tolist())) == 3:   # A pole's quad keeps its one real triangle.
                    tris.append(shared)
                    corners.append(tri)
    tris, corners = np.asarray(tris, np.uint32), np.asarray(corners, np.int64)
    weights = None
    if groups:
        height = (positions[:, 2] - positions[:, 2].min()) / max(float(np.ptp(positions[:, 2])), 1e-9)
        centres = np.linspace(0.0, 1.0, len(groups))
        weights = np.maximum(0.0, 1.0 - np.abs(height[:, None] - centres[None]) * (len(groups) - 1))
        weights /= weights.sum(1, keepdims=True)
    return G.Geometry(positions, tris, uvs[corners].astype(np.float32), normals[corners].astype(np.float32), weights,
                      weight_names=list(groups) if groups else None)


def box(around):
    low, high = around.bounds()
    low, high = low - 0.05, high + 0.05
    corners = np.array([[(low, high)[x][0], (low, high)[y][1], (low, high)[z][2]]
                        for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return G.Geometry(corners, np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))], np.uint32))


class Read:
    """A ``game_ready_mesh.MeshRead`` stand-in: read geometry and how it was read."""

    def __init__(self, shape, skinned=False):
        self.shape, self.geometry = shape, surface_mesh(*shape[:3], groups=shape[3] if len(shape) > 3 else None)
        self.geometry.modifiers_not_applied = 0
        self.note = (("the base mesh in rest pose, because an Armature modifier deforms it (0 other modifiers not "
                      "applied), with the weights of the deforming groups that weigh something on it") if skinned else
                     "the evaluated mesh (every enabled modifier applied), as an export takes it")
        self.groups = list(shape[3]) if skinned else []

    def release(self):
        pass


def reduced(read, ratio):
    kind, nu, nv = read.shape[:3]
    k = math.sqrt(ratio)
    return surface_mesh(kind, max(3, round(nu * k)), max(2, round(nv * k)),
                        groups=read.shape[3] if len(read.shape) > 3 else None)


class Reducer:
    """``game_ready_mesh.Reducer`` stand-in: a coarser tessellation of the same surface at each ratio."""

    def __init__(self, read, *_weights):
        self.read, self.positions = read, read.geometry.positions
        self._last = None

    def refusal(self):
        return None

    def simplify(self, ratio, max_error, lock_border):
        self._last = reduced(self.read, ratio)   # The host asks for its geometry next.
        return self._last.tris.reshape(-1), 0.0123 * (1.0 - ratio), 3.4

    def geometry(self, indices):
        return self._last


def install(objects):
    """The host route with ``objects`` ({handle: Read}) standing in for the scene."""
    H._reduce = lambda: K
    H._mesh_object = lambda window, handle, what: types.SimpleNamespace(name=handle.split(":")[-1], handle=handle)
    M.read = lambda obj, depsgraph, unit_scale, kernels: objects[obj.handle]
    M.Reducer = Reducer
    M.decimate = lambda read, ratio, kernels, unit_scale: (reduced(read, ratio), 5.6)
    H._hull = lambda read, unit_scale: read.geometry   # Every source here is convex or near it; a stand-in only.


WINDOW = types.SimpleNamespace(scene=types.SimpleNamespace(unit_settings=types.SimpleNamespace(
    system="METRIC", scale_length=1.0)), view_layer=types.SimpleNamespace(depsgraph=None))
HANDLE = "scn:" + "a" * 32 + ":%d"
GROUPS = ("Hips", "Spine", "Chest", "Neck", "Head")


def game_ready_cases():
    """(name, arguments, {handle: Read}) for several unrelated requests."""
    sphere, torus, grid = ("sphere", 32, 16), ("torus", 36, 14), ("grid", 22, 22)
    capsule = ("capsule", 24, 20, GROUPS)
    h = [HANDLE % k for k in range(1, 9)]
    return [
        ("object only (sphere)", {"object": h[0]}, {h[0]: Read(sphere)}),
        ("object only (open grid)", {"object": h[0]}, {h[0]: Read(grid)}),
        ("two LOD objects (torus)", {"object": h[0], "lods": [h[1], h[2]], "switch_distances": [10, 25]},
         {h[0]: Read(torus), h[1]: Read(("torus", 24, 10)), h[2]: Read(("torus", 14, 6))}),
        ("simplify, both methods, 3 ratios (sphere)", {"object": h[0], "simplify": {"ratios": [0.5, 0.25, 0.1]}},
         {h[0]: Read(sphere)}),
        ("simplify, meshopt only, 2 ratios (grid)",
         {"object": h[0], "simplify": {"ratios": [0.5, 0.2], "methods": ["MESHOPT"]}}, {h[0]: Read(grid)}),
        ("collider only (torus)", {"object": h[0], "collider": h[1]},
         {h[0]: Read(torus), h[1]: types.SimpleNamespace(geometry=box(Read(torus).geometry), note="the evaluated "
                                                         "mesh (every enabled modifier applied), as an export takes it",
                                                         groups=[], release=lambda: None)}),
        ("skinned, simplify both methods, 2 ratios (capsule)",
         {"object": h[0], "simplify": {"ratios": [0.5, 0.25]}}, {h[0]: Read(capsule, skinned=True)}),
        ("everything: LOD object, 2 ratios, collider, distances (sphere)",
         {"object": h[0], "lods": [h[1]], "simplify": {"ratios": [0.5, 0.25]}, "collider": h[2],
          "switch_distances": [8, 20, 40]},
         {h[0]: Read(sphere), h[1]: Read(("sphere", 20, 10)),
          h[2]: types.SimpleNamespace(geometry=box(Read(sphere).geometry), note="the evaluated mesh (every enabled "
                                      "modifier applied), as an export takes it", groups=[], release=lambda: None)}),
    ]


def _member(result, key, ascii_text):
    """Bytes ``key`` and its value add to the compact JSON of ``result`` (its comma included)."""
    if key not in result:
        return 0
    whole = json.dumps(result, ensure_ascii=ascii_text, allow_nan=False, separators=(",", ":"))
    rest = json.dumps({k: v for k, v in result.items() if k != key}, ensure_ascii=ascii_text, allow_nan=False,
                      separators=(",", ":"))
    return len(whole.encode()) - len(rest.encode())


def measure_game_ready():
    rows = []
    for name, arguments, objects in game_ready_cases():
        install(objects)
        result = H.check(WINDOW, validate_game_ready(CHECK, arguments))
        shown = model_results.model_result(CHECK, result)
        row = {"case": name, "arguments": arguments}
        for route, ascii_text in (("codex", True), ("claude_direct", False)):
            text = json.dumps(shown, ensure_ascii=ascii_text, allow_nan=False, separators=(",", ":"))
            row[route] = {"bytes": len(text.encode()), "measured_bytes": _member(shown, "measured", ascii_text)}
        row["measured_keys"] = sorted(shown.get("measured") or ())
        rows.append(row)
    return rows


# Worker briefs --------------------------------------------------------------------------------------------------------

def _prose(seed, chars):
    """Deterministic plain prose of about ``chars`` characters (a request's sentences, not a test's wording)."""
    words = ("the", "a", "with", "and", "of", "warm", "light", "wooden", "stone", "roof", "window", "table", "scale",
             "keep", "match", "reference", "colour", "edge", "bevel", "low", "detail", "side", "front", "frame",
             "metal", "glass", "door", "path", "garden", "corner", "shelf", "lamp", "café", "naïve", "façade")
    rng = np.random.default_rng(seed)
    out, sentence = [], []
    while sum(len(w) + 1 for w in out) < chars:
        sentence.append(words[int(rng.integers(len(words)))])
        if len(sentence) > int(rng.integers(6, 18)):
            out.append(" ".join(sentence).capitalize() + ".")
            sentence = []
            if rng.random() < 0.15:
                out.append("\n")
    return " ".join(out)[:chars]


HISTORIES = {
    "one short message": [_prose(1, 140)],
    "long brief and two replies": [_prose(2, 6000), "Oak.", "Yes, go ahead with the warmer lamps."],
    "past the cap (10 messages of 4,000)": [_prose(10 + k, 4000) for k in range(10)],
}
SCHEMAS = {"no schemas": 0, "10.8 KB schemas": 10800}
COUNTS = (1, 2, 4, 8, 16)


def _part(number):
    return {"name": "Part %d" % number, "brief": _prose(100 + number, 300 + 170 * (number % 5))}


def _schemas_block(size):
    if not size:
        return lambda room: ""
    text = worker_schemas_header() + "\n" + json.dumps({"filler": "x" * max(0, size - 64)}, separators=(",", ":"))
    return lambda room: text[:0] if len(text.encode()) > room else text


def worker_schemas_header():
    from angelo_agent.worker_schemas import HEADER
    return HEADER


def first_message(number, history, schemas):
    from angelo_agent.worker_sessions import BRIEF
    part = _part(number)
    head = BRIEF.format(name=part["name"], brief=part["brief"])
    head += "\n\nThe images attached are the person's, sent with the request that started this part."
    return worker_request.joined(head, history, schemas)


def _common_prefix(texts):
    first = texts[0]
    size = len(first)
    for other in texts[1:]:
        k = 0
        limit = min(size, len(other))
        while k < limit and first[k] == other[k]:
            k += 1
        size = k
    return len(first[:size].encode())


def measure_briefs():
    rows = []
    for history_name, history in HISTORIES.items():
        for schemas_name, size in SCHEMAS.items():
            for count in COUNTS:
                texts = [first_message(n, history, _schemas_block(size)) for n in range(1, count + 1)]
                sizes = [len(t.encode()) for t in texts]
                shared = _common_prefix(texts) if count > 1 else 0
                # Bytes no earlier worker's first message began with: worker 1 whole, each later one past the prefix.
                new = sizes[0] + sum(s - shared for s in sizes[1:])
                rows.append({"history": history_name, "schemas": schemas_name, "workers": count,
                             "history_chars": sum(len(m) for m in history),
                             "first_message_bytes_mean": round(sum(sizes) / count),
                             "first_message_bytes_max": max(sizes),
                             "shared_prefix_bytes": shared, "batch_bytes": sum(sizes), "batch_bytes_new": new})
    return rows


def main():
    report = {"game_ready": measure_game_ready(), "briefs": measure_briefs()}
    print("Game-ready results (bytes of the model's copy; measured = the glossary member)")
    for row in report["game_ready"]:
        codex, claude = row["codex"], row["claude_direct"]
        print("  %-62s codex %6d (measured %5d)  claude %6d (measured %5d)  keys %d" % (
            row["case"], codex["bytes"], codex["measured_bytes"], claude["bytes"], claude["measured_bytes"],
            len(row["measured_keys"])))
    print("Worker first messages (bytes)")
    for row in report["briefs"]:
        print("  %-38s %-16s x%-2d  each %6d  shared %6d  batch %7d  new %7d" % (
            row["history"], row["schemas"], row["workers"], row["first_message_bytes_mean"],
            row["shared_prefix_bytes"], row["batch_bytes"], row["batch_bytes_new"]))
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
