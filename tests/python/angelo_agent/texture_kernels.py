# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender 5.2's Noise, Voronoi, Brick and Checker textures, computed as Cycles does, for many samples at once.

A NumPy port of Cycles' own kernels, kept in their float32 arithmetic and
evaluation order so that the grader reads the same values the renderer
shows (``shader_graph`` calls ``evaluate`` with a column of inputs per
socket, one row per sample):

* hashing: ``intern/cycles/util/hash.h`` (Jenkins lookup3 ``hash_uint*``, the
  PCG ``hash_int3_to_float3``);
* Perlin noise and fBM: ``kernel/svm/noise.h`` (``perlin_3d``, ``snoise_3d``)
  and ``kernel/svm/fractal_noise.h`` (``noise_fbm``), the Noise texture node
  ``kernel/svm/noisetex.h`` (distortion, the colour's random offsets);
* Voronoi: ``kernel/svm/voronoi.h`` (F1, F2 and distance to edge, every
  metric, the fractal octaves of detail, roughness and lacunarity,
  normalisation);
* Brick: ``kernel/svm/brick.h`` (``svm_brick``, ``brick_noise``); Checker:
  ``kernel/svm/checker.h``.

Only the variants the recorded scenes use are ported: 3D noise of the fBM
type, 3D Voronoi. Anything else raises ``Unsupported`` and the grader reads
that texture as unknown. EEVEE implements the same functions in GLSL.
"""

import numpy as np

F32 = np.float32
U32 = np.uint32
I32 = np.int32


class Unsupported(Exception):
    """A texture variant this module does not compute; the message names it."""


def f32(values):
    return np.asarray(values, dtype=F32)


# ---------------------------------------------------------------- hashing (util/hash.h)

def _rot(x, k):
    return (x << U32(k)) | (x >> U32(32 - k))


def _final(a, b, c):
    c ^= b
    c -= _rot(b, 14)
    a ^= c
    a -= _rot(c, 11)
    b ^= a
    b -= _rot(a, 25)
    c ^= b
    c -= _rot(b, 16)
    a ^= c
    a -= _rot(c, 4)
    b ^= a
    b -= _rot(a, 14)
    c ^= b
    c -= _rot(b, 24)
    return a, b, c


def _mix(a, b, c):
    a -= c
    a ^= _rot(c, 4)
    c += b
    b -= a
    b ^= _rot(a, 6)
    a += c
    c -= b
    c ^= _rot(b, 8)
    b += a
    a -= c
    a ^= _rot(c, 16)
    c += b
    b -= a
    b ^= _rot(a, 19)
    a += c
    c -= b
    c ^= _rot(b, 4)
    b += a
    return a, b, c


def _start(count, shape):
    value = U32((0xDEADBEEF + (count << 2) + 13) & 0xFFFFFFFF)
    return (np.full(shape, value, dtype=U32), np.full(shape, value, dtype=U32), np.full(shape, value, dtype=U32))


def as_uint(values):
    """The bits of int32 or float32 values as uint32 (``__float_as_uint`` and the int-to-uint casts)."""
    values = np.asarray(values)
    if values.dtype == F32:
        return values.view(U32)
    return values.astype(I32).view(U32)


def hash_uint2(kx, ky):
    with np.errstate(over="ignore"):
        a, b, c = _start(2, np.broadcast(kx, ky).shape)
        b += ky
        a += kx
        return _final(a, b, c)[2]


def hash_uint3(kx, ky, kz):
    with np.errstate(over="ignore"):
        a, b, c = _start(3, np.broadcast(kx, ky, kz).shape)
        c += kz
        b += ky
        a += kx
        return _final(a, b, c)[2]


def hash_uint4(kx, ky, kz, kw):
    with np.errstate(over="ignore"):
        a, b, c = _start(4, np.broadcast(kx, ky, kz, kw).shape)
        a += kx
        b += ky
        c += kz
        a, b, c = _mix(a, b, c)
        a += kw
        return _final(a, b, c)[2]


def uint_to_float_incl(n):
    return np.asarray(n).astype(F32) * (F32(1.0) / F32(0xFFFFFFFF))


def hash_float2_to_float(x, y):
    return uint_to_float_incl(hash_uint2(as_uint(f32(x)), as_uint(f32(y))))


def hash_int3_to_float3(x, y, z):
    """PCG 3D (``hash_pcg3d_i``) of int32 cells to three floats in [0, 1]."""
    with np.errstate(over="ignore"):
        v = [np.asarray(c, dtype=I32) * I32(1664525) + I32(1013904223) for c in (x, y, z)]
        v[0] = v[0] + v[1] * v[2]
        v[1] = v[1] + v[2] * v[0]
        v[2] = v[2] + v[0] * v[1]
        v = [c ^ (c >> I32(16)) for c in v]
        v[0] = v[0] + v[1] * v[2]
        v[1] = v[1] + v[2] * v[0]
        v[2] = v[2] + v[0] * v[1]
        scale = F32(1.0) / F32(0x7FFFFFFF)
        return [(c & I32(0x7FFFFFFF)).astype(F32) * scale for c in v]


# ---------------------------------------------------------------- Perlin noise (svm/noise.h)

def _fade(t):
    return t * t * t * (t * (t * F32(6.0) - F32(15.0)) + F32(10.0))


def _grad3(hash_value, x, y, z):
    h = (hash_value & U32(15)).astype(I32)
    u = np.where(h < 8, x, y)
    vt = np.where((h == 12) | (h == 14), x, z)
    v = np.where(h < 4, y, vt)
    return np.where(h & 1, -u, u) + np.where(h & 2, -v, v)


def perlin_3d(x, y, z):
    floors = [np.floor(c) for c in (x, y, z)]
    X, Y, Z = (c.astype(I32) for c in floors)
    fx, fy, fz = (c - f for c, f in zip((x, y, z), floors))
    u, v, w = _fade(fx), _fade(fy), _fade(fz)
    one = F32(1.0)

    def corner(dx, dy, dz):
        h = hash_uint3(as_uint(X + I32(dx)), as_uint(Y + I32(dy)), as_uint(Z + I32(dz)))
        return _grad3(h, fx - F32(dx), fy - F32(dy), fz - F32(dz))
    v0, v1, v2, v3 = corner(0, 0, 0), corner(1, 0, 0), corner(0, 1, 0), corner(1, 1, 0)
    v4, v5, v6, v7 = corner(0, 0, 1), corner(1, 0, 1), corner(0, 1, 1), corner(1, 1, 1)
    x1, y1, z1 = one - u, one - v, one - w
    return (z1 * (y1 * (v0 * x1 + v1 * u) + v * (v2 * x1 + v3 * u))
            + w * (y1 * (v4 * x1 + v5 * u) + v * (v6 * x1 + v7 * u)))


def snoise_3d(p):
    """``snoise_3d``: signed Perlin noise, repeated every 100000 on each axis."""
    p = [f32(c) for c in p]
    corrected = [np.fmod(c, F32(100000.0)) + np.where(np.abs(c) >= F32(1000000.0), F32(0.5), F32(0.0)) for c in p]
    return F32(0.9820) * perlin_3d(*corrected)


def noise_fbm_3d(p, detail, roughness, lacunarity, normalize):
    """``noise_fbm`` for one set of (scalar) parameters over many points ``p`` (three float32 arrays)."""
    fscale, amp, maxamp = F32(1.0), F32(1.0), F32(0.0)
    total = np.zeros_like(p[0])
    for _ in range(int(detail) + 1):
        t = snoise_3d([fscale * c for c in p])
        total = total + t * amp
        maxamp = maxamp + amp
        amp = amp * roughness
        fscale = fscale * lacunarity
    remainder = F32(detail - np.floor(detail))
    if remainder != 0.0:
        t = snoise_3d([fscale * c for c in p])
        total2 = total + t * amp
        if normalize:
            low = F32(0.5) * total / maxamp + F32(0.5)
            high = F32(0.5) * total2 / (maxamp + amp) + F32(0.5)
            return low + remainder * (high - low)
        return total + remainder * (total2 - total)
    return F32(0.5) * total / maxamp + F32(0.5) if normalize else total


def random_float3_offset(seed):
    return [F32(100.0) + hash_float2_to_float(seed, axis) * F32(100.0) for axis in (0.0, 1.0, 2.0)]


def noise_texture(node, columns):
    """The Noise texture's Fac and Color (``svm_node_tex_noise``, 3D)."""
    if getattr(node, "noise_dimensions", "3D") != "3D":
        raise Unsupported("a %s Noise texture" % node.noise_dimensions)
    if getattr(node, "noise_type", "FBM") != "FBM":
        raise Unsupported("a %s Noise texture" % node.noise_type)
    vector, scale = columns["Vector"], columns["Scale"]
    detail = np.clip(columns["Detail"], F32(0.0), F32(15.0))
    roughness = np.maximum(columns["Roughness"], F32(0.0))
    lacunarity, distortion = columns["Lacunarity"], columns["Distortion"]
    normalize = bool(getattr(node, "normalize", True))
    count = len(scale)
    fac, colour = np.zeros(count, dtype=F32), np.zeros((count, 3), dtype=F32)
    groups = np.unique(np.stack([detail, roughness, lacunarity, distortion], axis=1), axis=0, return_inverse=True)
    for index, (d, r, l, dist) in enumerate(groups[0]):
        rows = np.nonzero(groups[1].reshape(-1) == index)[0]
        p = [vector[rows, axis] * scale[rows] for axis in range(3)]
        d, r, l, dist = F32(d), F32(r), F32(l), F32(dist)
        if dist != 0.0:
            offsets = [random_float3_offset(F32(seed)) for seed in (0.0, 1.0, 2.0)]
            shifts = [snoise_3d([c + o for c, o in zip(p, offset)]) * dist for offset in offsets]
            p = [c + s for c, s in zip(p, shifts)]
        value = noise_fbm_3d(p, d, r, l, normalize)
        fac[rows] = value
        colour[rows, 0] = value
        for axis, seed in ((1, 3.0), (2, 4.0)):
            offset = random_float3_offset(F32(seed))
            colour[rows, axis] = noise_fbm_3d([c + o for c, o in zip(p, offset)], d, r, l, normalize)
    return {"Fac": fac, "Color": colour}


# ---------------------------------------------------------------- Voronoi (svm/voronoi.h)

def _metric_distance(dx, dy, dz, metric, exponent, bound):
    if metric == "EUCLIDEAN":
        squared = dx * dx + dy * dy + dz * dz
        return squared if bound else np.sqrt(squared)
    if metric == "MANHATTAN":
        return np.abs(dx) + np.abs(dy) + np.abs(dz)
    if metric == "CHEBYCHEV":
        return np.maximum(np.maximum(np.abs(dx), np.abs(dy)), np.abs(dz))
    if metric == "MINKOWSKI":
        summed = np.abs(dx) ** exponent + np.abs(dy) ** exponent + np.abs(dz) ** exponent
        return summed if bound else summed ** (F32(1.0) / exponent)
    raise Unsupported("the %s Voronoi metric" % metric)


_OFFSETS = [(i, j, k) for k in (-1, 0, 1) for j in (-1, 0, 1) for i in (-1, 0, 1)]


def _cells(coord):
    floors = [np.floor(c) for c in coord]
    return floors, [c - f for c, f in zip(coord, floors)], [f.astype(I32) for f in floors]


def voronoi_f1_f2(coord, randomness, metric, exponent, second):
    """``voronoi_f1`` (or ``voronoi_f2``) in 3D: distance, colour and position of the nearest (second) point."""
    floors, local, cell = _cells(coord)
    shape = local[0].shape
    best = [np.full(shape, np.finfo(F32).max, dtype=F32), np.full(shape, np.finfo(F32).max, dtype=F32)]
    offsets = [np.zeros(shape + (3,), dtype=I32), np.zeros(shape + (3,), dtype=I32)]
    points = [np.zeros(shape + (3,), dtype=F32), np.zeros(shape + (3,), dtype=F32)]
    for i, j, k in _OFFSETS:
        jitter = hash_int3_to_float3(cell[0] + I32(i), cell[1] + I32(j), cell[2] + I32(k))
        point = [F32(o) + h * randomness for o, h in zip((i, j, k), jitter)]
        d = _metric_distance(point[0] - local[0], point[1] - local[1], point[2] - local[2], metric, exponent, True)
        here = np.stack(point, axis=-1)
        offset = np.array([i, j, k], dtype=I32)
        if not second:
            closer = d < best[0]
            best[0] = np.where(closer, d, best[0])
            offsets[0][closer] = offset
            points[0][closer] = here[closer]
            continue
        first = d < best[0]
        between = (~first) & (d < best[1])
        best[1] = np.where(first, best[0], np.where(between, d, best[1]))
        offsets[1][first] = offsets[0][first]
        points[1][first] = points[0][first]
        offsets[1][between] = offset
        points[1][between] = here[between]
        best[0] = np.where(first, d, best[0])
        offsets[0][first] = offset
        points[0][first] = here[first]
    pick = 1 if second else 0
    target, offset = points[pick], offsets[pick]
    distance = _metric_distance(target[..., 0] - local[0], target[..., 1] - local[1], target[..., 2] - local[2],
                                metric, exponent, False)
    colour = hash_int3_to_float3(cell[0] + offset[..., 0], cell[1] + offset[..., 1], cell[2] + offset[..., 2])
    position = [target[..., axis] + floors[axis] for axis in range(3)]
    return distance, np.stack(colour, axis=-1), np.stack(position, axis=-1)


def voronoi_distance_to_edge(coord, randomness):
    floors, local, cell = _cells(coord)
    shape = local[0].shape
    vectors = []
    for i, j, k in _OFFSETS:
        jitter = hash_int3_to_float3(cell[0] + I32(i), cell[1] + I32(j), cell[2] + I32(k))
        vectors.append([F32(o) + h * randomness - l for o, h, l in zip((i, j, k), jitter, local)])
    closest, minimum = [np.zeros(shape, dtype=F32) for _ in range(3)], np.full(shape, np.finfo(F32).max, dtype=F32)
    for vector in vectors:
        d = vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]
        closer = d < minimum
        minimum = np.where(closer, d, minimum)
        closest = [np.where(closer, v, c) for v, c in zip(vector, closest)]
    minimum = np.full(shape, np.finfo(F32).max, dtype=F32)
    for vector in vectors:
        perpendicular = [v - c for v, c in zip(vector, closest)]
        length_sq = perpendicular[0] ** 2 + perpendicular[1] ** 2 + perpendicular[2] ** 2
        valid = length_sq > F32(0.0001)
        length = np.sqrt(np.where(valid, length_sq, F32(1.0)))
        distance = sum(((c + v) / F32(2.0)) * (p / length) for c, v, p in zip(closest, vector, perpendicular))
        minimum = np.where(valid, np.minimum(minimum, distance), minimum)
    return minimum


def voronoi_texture(node, columns):
    """The Voronoi texture's Distance, Color and Position (``svm_node_tex_voronoi``, 3D)."""
    if getattr(node, "voronoi_dimensions", "3D") != "3D":
        raise Unsupported("a %s Voronoi texture" % node.voronoi_dimensions)
    feature, metric = getattr(node, "feature", "F1"), getattr(node, "distance", "EUCLIDEAN")
    if feature not in {"F1", "F2", "DISTANCE_TO_EDGE"}:
        raise Unsupported("the %s Voronoi feature" % feature)
    vector, scale = columns["Vector"], columns["Scale"]
    detail = np.clip(columns["Detail"], F32(0.0), F32(15.0))
    roughness = np.clip(columns["Roughness"], F32(0.0), F32(1.0))
    randomness = np.clip(columns["Randomness"], F32(0.0), F32(1.0))
    lacunarity = columns["Lacunarity"]
    exponent = columns.get("Exponent", np.full(len(scale), F32(0.5)))
    normalize = bool(getattr(node, "normalize", False))
    count = len(scale)
    out_distance = np.zeros(count, dtype=F32)
    out_colour, out_position = np.zeros((count, 3), dtype=F32), np.zeros((count, 3), dtype=F32)
    keys = np.stack([detail, roughness, randomness, lacunarity, exponent, scale], axis=1)
    groups = np.unique(keys, axis=0, return_inverse=True)
    for index, (d, r, rand, lac, exp, sc) in enumerate(groups[0]):
        rows = np.nonzero(groups[1].reshape(-1) == index)[0]
        d, r, rand, lac, exp, sc = (F32(v) for v in (d, r, rand, lac, exp, sc))
        coord = [vector[rows, axis] * sc for axis in range(3)]
        zero_input = d == 0.0 or r == 0.0
        half = F32(0.5) + F32(0.5) * rand
        if feature == "DISTANCE_TO_EDGE":
            max_distance = half
            amplitude, max_amplitude, octave_scale = F32(1.0), max_distance, F32(1.0)
            distance = np.full(len(rows), F32(8.0), dtype=F32)
            for octave in range(int(np.ceil(d)) + 1):
                octave_distance = voronoi_distance_to_edge([c * octave_scale for c in coord], rand)
                if zero_input:
                    distance = octave_distance
                    break
                if octave <= d:
                    max_amplitude = max_amplitude + amplitude * (max_distance / octave_scale - max_amplitude)
                    target = np.minimum(distance, octave_distance / octave_scale)
                    distance = distance + amplitude * (target - distance)
                    octave_scale, amplitude = octave_scale * lac, amplitude * r
                else:
                    remainder = F32(d - np.floor(d))
                    if remainder != 0.0:
                        lerp_amplitude = max_amplitude + amplitude * (max_distance / octave_scale - max_amplitude)
                        max_amplitude = max_amplitude + remainder * (lerp_amplitude - max_amplitude)
                        lerp_distance = distance + amplitude * (np.minimum(distance, octave_distance / octave_scale)
                                                                - distance)
                        distance = distance + remainder * (np.minimum(distance, lerp_distance) - distance)
            if normalize:
                distance = distance / max_amplitude
            out_distance[rows] = distance
            continue
        max_distance = _metric_distance(half, half, half, metric, exp, False) * (F32(2.0) if feature == "F2"
                                                                                    else F32(1.0))
        amplitude, max_amplitude, octave_scale = F32(1.0), F32(0.0), F32(1.0)
        distance = np.zeros(len(rows), dtype=F32)
        colour, position = np.zeros((len(rows), 3), dtype=F32), np.zeros((len(rows), 3), dtype=F32)
        for octave in range(int(np.ceil(d)) + 1):
            o_distance, o_colour, o_position = voronoi_f1_f2([c * octave_scale for c in coord], rand, metric, exp,
                                                             feature == "F2")
            if zero_input:
                max_amplitude, distance, colour, position = F32(1.0), o_distance, o_colour, o_position
                break
            if octave <= d:
                max_amplitude = max_amplitude + amplitude
                distance = distance + o_distance * amplitude
                colour = colour + o_colour * amplitude
                position = position + amplitude * (o_position / octave_scale - position)
                octave_scale, amplitude = octave_scale * lac, amplitude * r
            else:
                remainder = F32(d - np.floor(d))
                if remainder != 0.0:
                    max_amplitude = max_amplitude + remainder * amplitude
                    distance = distance + remainder * (o_distance * amplitude)
                    colour = colour + remainder * (o_colour * amplitude)
                    mixed = position + amplitude * (o_position / octave_scale - position)
                    position = position + remainder * (mixed - position)
        if normalize:
            distance = distance / (max_amplitude * max_distance)
            colour = colour / max_amplitude
        out_distance[rows], out_colour[rows] = distance, colour
        out_position[rows] = position / sc if sc != 0.0 else F32(0.0)
    return {"Distance": out_distance, "Color": out_colour, "Position": out_position,
            "W": np.zeros(count, dtype=F32)}


# ---------------------------------------------------------------- Brick (svm/brick.h)

def brick_noise(n):
    with np.errstate(over="ignore"):
        n = (np.asarray(n).astype(U32) + U32(1013)) & U32(0x7FFFFFFF)
        n = (n >> U32(13)) ^ n
        nn = (n * (n * n * U32(60493) + U32(19990303)) + U32(1376312589)) & U32(0x7FFFFFFF)
        return F32(0.5) * (nn.astype(F32) / F32(1073741824.0))


def brick_texture(node, columns):
    """The Brick texture's Color and Fac (``svm_node_tex_brick``)."""
    vector, scale = columns["Vector"], columns["Scale"]
    p = [vector[:, axis] * scale for axis in range(3)]
    mortar_size, mortar_smooth, bias = columns["Mortar Size"], columns["Mortar Smooth"], columns["Bias"]
    brick_width, row_height = columns["Brick Width"].copy(), columns["Row Height"]
    offset_amount, squash_amount = F32(getattr(node, "offset", 0.5)), F32(getattr(node, "squash", 1.0))
    offset_frequency, squash_frequency = int(getattr(node, "offset_frequency", 2)), int(getattr(node, "squash_frequency", 2))
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        rownum = np.floor(p[1] / row_height).astype(I32)
        offset = np.zeros_like(p[0])
        if offset_frequency and squash_frequency:
            brick_width = np.where(np.fmod(rownum, squash_frequency) != 0, brick_width, brick_width * squash_amount)
            offset = np.where(np.fmod(rownum, offset_frequency) != 0, F32(0.0), brick_width * offset_amount)
        bricknum = np.floor((p[0] + offset) / brick_width).astype(I32)
        x = (p[0] + offset) - brick_width * bricknum.astype(F32)
        y = p[1] - row_height * rownum.astype(F32)
        seed = (rownum.astype(np.int64) << 16) + (bricknum.astype(np.int64) & 0xFFFF)
        tint = np.clip(brick_noise((seed & 0xFFFFFFFF).astype(np.uint64).astype(U32)) + bias, F32(0.0), F32(1.0))
        min_dist = np.minimum(np.minimum(x, y), np.minimum(brick_width - x, row_height - y))
        smooth_input = (F32(1.0) - min_dist / mortar_size) / mortar_smooth
        smoothed = np.where(smooth_input <= 0.0, F32(0.0), np.where(
            smooth_input >= 1.0, F32(1.0), F32(3.0) * smooth_input ** 2 - F32(2.0) * smooth_input ** 3))
        mortar = np.where(min_dist >= mortar_size, F32(0.0), np.where(mortar_smooth == 0.0, F32(1.0), smoothed))
    colour1, colour2, mortar_colour = columns["Color1"], columns["Color2"], columns["Mortar"]
    tinted = np.where((mortar != 1.0)[:, None], (F32(1.0) - tint)[:, None] * colour1 + tint[:, None] * colour2,
                      colour1)
    colour = tinted * (F32(1.0) - mortar)[:, None] + mortar_colour * mortar[:, None]
    return {"Color": colour.astype(F32), "Fac": mortar.astype(F32)}


# ---------------------------------------------------------------- Checker (svm/checker.h)

def checker_texture(node, columns):
    """The Checker texture's Color and Fac (``svm_node_tex_checker``)."""
    scale = columns["Scale"]
    p = [(columns["Vector"][:, axis] * scale + F32(0.000001)) * F32(0.999999) for axis in range(3)]
    xi, yi, zi = (np.abs(np.floor(c).astype(I32)) for c in p)
    fac = np.where((xi % 2 == yi % 2).astype(I32) == zi % 2, F32(1.0), F32(0.0)).astype(F32)
    colour = np.where((fac == 1.0)[:, None], columns["Color1"], columns["Color2"])
    return {"Color": colour.astype(F32), "Fac": fac}


KERNELS = {"TEX_NOISE": noise_texture, "TEX_VORONOI": voronoi_texture, "TEX_BRICK": brick_texture,
           "TEX_CHECKER": checker_texture}
VECTOR_INPUTS = {"Vector"}


def evaluate(node, columns):
    """``{output identifier: array}`` of a texture node for every row of ``columns`` (input identifier ->
    float32 array, (n,) for values and (n, 3) for vectors and colours); raises ``Unsupported``."""
    kernel = KERNELS.get(node.type)
    if kernel is None:
        raise Unsupported("%s node %r" % (node.type, node.name))
    mapping = getattr(node, "texture_mapping", None)
    if mapping is not None and not (all(abs(v) < 1e-9 for v in mapping.translation)
                                    and all(abs(v) < 1e-9 for v in mapping.rotation)
                                    and all(abs(v - 1.0) < 1e-9 for v in mapping.scale)):
        raise Unsupported("%s node %r with its own texture mapping" % (node.type, node.name))
    return kernel(node, {key: f32(value) for key, value in columns.items()})
