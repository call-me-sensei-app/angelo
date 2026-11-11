# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which map of a texture set is which: names first, checked and completed by what the pixels hold.

A PBR texture set (Megascans surfaces and decals, every seller's material zips) is a folder of images. ``classify``
gives each image a role for one Principled material:

* **Names** (``role_from_name``) use the map-type words every major texture source writes, as a whole word of the
  file stem, nearest the end winning ("rock_albedo_2k", "RockWall_Normal_DX", "T_Rock_ORM"): base colour
  (albedo, basecolor, diffuse, color...), normal, roughness, gloss, metalness, ambient occlusion, displacement/height,
  opacity, specular, cavity, translucency, emission, bump, and channel-packed ORM/ARM/MRA/RMA maps. The single
  letters of the Unreal texture naming convention (T_Name_D/_N/_R...) count only on a ``T_`` file. Resolution and
  level words (2k, 4096, LOD0) are skipped.
* **Pixels** (``features``, from a small sample) say whether a map is grey, whether it is a tangent-space normal map
  (unit vectors around +Z), whether its alpha varies and whether it is near binary. A map named normal that is not
  one is not used as one; an unnamed normal-like map is the normal; an unnamed colour map is the base colour when it
  is the only one. ``normal_convention`` tells an OpenGL (+Y) from a DirectX (-Y) normal map by which reading makes
  the map the gradient of a height field (the curl test), over the name's ``gl``/``dx`` word, then OpenGL.

No file name of one listing decides anything; the vocabulary is the industry's, and the tests hold out sources the
rules were not developed on. Standard library plus numpy (Blender bundles it); no bpy.
"""

import re

ROLES = ('base_color', 'normal', 'roughness', 'gloss', 'metallic', 'ao', 'displacement', 'opacity', 'specular',
         'cavity', 'translucency', 'emission', 'bump', 'packed')
_WORDS = {
    'base_color': ('albedo', 'basecolor', 'basecolour', 'diffuse', 'diff', 'color', 'colour', 'col', 'alb', 'base'),
    'normal': ('normal', 'normals', 'nrm', 'nor', 'nml', 'norm', 'normalmap', 'normalgl', 'normaldx',
               'normalogl', 'normalopengl', 'normaldirectx'),
    'roughness': ('roughness', 'rough', 'rgh'),
    'gloss': ('gloss', 'glossiness', 'smoothness'),
    'metallic': ('metallic', 'metalness', 'metal', 'met'),
    'ao': ('ao', 'ambientocclusion', 'occlusion', 'occ', 'mixedao'),
    'displacement': ('displacement', 'disp', 'height', 'heightmap', 'dsp', 'parallax'),
    'opacity': ('opacity', 'alpha', 'transparency', 'cutout', 'mask'),
    'specular': ('specular', 'spec', 'spc', 'reflection', 'refl'),
    'cavity': ('cavity', 'cav', 'curvature'),
    'translucency': ('translucency', 'transmission', 'subsurface', 'sss', 'thickness', 'scattering'),
    'emission': ('emission', 'emissive', 'emit', 'glow', 'illumination'),
    'bump': ('bump', 'bumpmap'),
}
NAMED_NORMAL_SCORE = 0.5   # A map named normal: at least this share of its pixels are normals (UV islands of a bake).
SCALAR = frozenset({'roughness', 'gloss', 'metallic', 'ao', 'displacement', 'opacity', 'specular', 'cavity', 'bump'})
# Channel-packed maps: which role each of R, G, B holds.
PACKED = {'orm': ('ao', 'roughness', 'metallic'), 'arm': ('ao', 'roughness', 'metallic'),
          'mra': ('metallic', 'roughness', 'ao'), 'rma': ('roughness', 'metallic', 'ao'),
          'occlusionroughnessmetallic': ('ao', 'roughness', 'metallic')}
# Unreal's texture naming convention: one letter after a T_ prefix.
_UNREAL = {'d': 'base_color', 'bc': 'base_color', 'b': 'base_color', 'a': 'base_color', 'n': 'normal', 'r': 'roughness',
           'm': 'metallic', 'h': 'displacement', 'e': 'emission', 'o': 'ao', 's': 'specular'}
_LOOKUP = {word: role for role, words in _WORDS.items() for word in words}
_TWO_WORD = {('base', 'color'): 'basecolor', ('base', 'colour'): 'basecolour', ('ambient', 'occlusion'): 'ao',
             ('height', 'map'): 'height', ('normal', 'map'): 'normal', ('normal', 'gl'): 'normalgl',
             ('normal', 'dx'): 'normaldx', ('normal', 'opengl'): 'normalgl', ('normal', 'directx'): 'normaldx',
             ('mixed', 'ao'): 'ao', ('bump', 'map'): 'bump'}
_SKIP = re.compile(r'\d+k|\d{3,5}(?:px)?|\d+x\d+|lod\d+|v\d+|var\d+|\d+|srgb|linear|lin|8bit|16bit|32bit|hi|lo|'
                   r'high|mid|low|raw|preview|thumb|tex|texture|map|t')


def words(stem):
    """Lower-case words of a file stem, split at separators and camelCase."""
    stem = re.sub(r'([a-z])([A-Z])', r'\1 \2', stem)
    stem = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', stem)
    return [w for w in re.split(r'[^0-9A-Za-z]+', stem.lower()) if w]


def role_from_name(name):
    """``(role, convention)`` from a file name: role in ``ROLES`` or None; convention 'gl', 'dx' or None.

    For a packed map the role is 'packed' and the convention is its channel order key (``PACKED``)."""
    stem = name.rpartition('/')[2].rsplit('.', 1)[0]
    parts = words(stem)
    convention = 'dx' if ({'dx', 'directx'} & set(parts)) or any(p.endswith('dx') and p.startswith('normal')
                                                                   for p in parts) else None
    if {'gl', 'ogl', 'opengl'} & set(parts) or any(p in ('normalgl', 'normalogl', 'normalopengl') for p in parts):
        convention = 'gl'
    merged, index = [], 0
    while index < len(parts):
        pair = tuple(parts[index:index + 2])
        if pair in _TWO_WORD:
            merged.append(_TWO_WORD[pair])
            index += 2
        else:
            merged.append(parts[index])
            index += 1
    for word in reversed(merged):
        if word in PACKED:
            return 'packed', word
        if word in _LOOKUP:
            role = _LOOKUP[word]
            return role, (convention if role == 'normal' else None)
        if word in ('dx', 'gl', 'ogl', 'opengl', 'directx') or _SKIP.fullmatch(word):
            continue
    original = re.split(r'[^0-9A-Za-z]+', stem)
    if len(original) >= 3 and original[0].lower() == 't' and original[-1].lower() in _UNREAL:
        role = _UNREAL[original[-1].lower()]
        return role, None
    return None, None


def features(pixels):
    """Facts of an ``(h, w, channels)`` float array in 0..1 (a small sample of the map, rows bottom to top)."""
    import numpy as np
    pixels = np.asarray(pixels, dtype=np.float64)
    if pixels.ndim == 2:
        pixels = pixels[:, :, None]
    rgb = pixels[:, :, :3] if pixels.shape[2] >= 3 else np.repeat(pixels[:, :, :1], 3, axis=2)
    spread = np.abs(rgb - rgb.mean(axis=2, keepdims=True)).max(axis=2)
    gray = bool(np.percentile(spread, 95) < 0.02)
    valid = _valid_normals(rgb)
    mean = rgb.reshape(-1, 3).mean(axis=0)
    score = float(np.mean(valid))
    inside = rgb[valid].mean(axis=0) if valid.any() else mean
    # By content alone a map is a normal map only when almost all of it is: unit vectors around +Z.
    normal_like = bool(not gray and score > 0.85 and inside[2] > 0.7 and abs(inside[0] - 0.5) < 0.15
                       and abs(inside[1] - 0.5) < 0.15)
    alpha = pixels[:, :, 3] if pixels.shape[2] >= 4 else None
    alpha_varies = bool(alpha is not None and float(alpha.min()) < 0.95)
    value = rgb.mean(axis=2)
    binary = bool(np.mean((value < 0.08) | (value > 0.92)) > 0.9)
    return {'gray': gray, 'normal_like': normal_like, 'normal_score': round(score, 4), 'alpha_varies': alpha_varies,
            'binary': binary, 'mean': [round(float(x), 4) for x in mean]}


def _valid_normals(rgb):
    """Pixels that decode to a tangent-space normal: near unit length (a downsampled bumpy map averages to a little
    less), pointing out of the surface. A baked model map has them only inside its UV islands."""
    import numpy as np
    vectors = rgb * 2.0 - 1.0
    length = np.linalg.norm(vectors, axis=2)
    return (length > 0.6) & (length < 1.15) & (vectors[:, :, 2] > 0.2)


def normal_convention(pixels):
    """'gl' or 'dx' when the curl test is decisive for this normal-map sample, else None."""
    import numpy as np
    colour = np.asarray(pixels, dtype=np.float64)[:, :, :3]
    valid = _valid_normals(colour)
    rgb = colour * 2.0 - 1.0
    z = np.clip(rgb[:, :, 2], 0.2, None)
    gx, gy = -rgb[:, :, 0] / z, -rgb[:, :, 1] / z
    # Only where a pixel and its right and upper neighbours are all normals (padding around UV islands is not).
    use = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1]
    if not use.any():
        return None

    def curl(sign):
        # A real height field's gradient has no curl: d(gy)/dx == d(gx)/dy (rows run bottom to top).
        difference = np.diff(sign * gy, axis=1)[:-1, :] - np.diff(gx, axis=0)[:, :-1]
        return float(np.mean(difference[use] ** 2))
    gl, dx = curl(1.0), curl(-1.0)
    if min(gl, dx) <= 0 and max(gl, dx) <= 0:
        return None
    if gl * 2.0 < dx:
        return 'gl'
    if dx * 2.0 < gl:
        return 'dx'
    return None


def classify(maps):
    """Roles for a texture set. ``maps``: ``[{'name', 'features', 'pixels' (optional, normal check), 'size'}]``.

    Returns ``{'roles': {role: name}, 'packed': {name: (r, g, b roles)}, 'normal': 'gl'|'dx'|None,
    'unused': [names], 'notes': [codes]}``. Raises ValueError when no map can colour or shape the material."""
    claims, notes, unused, packed = {}, [], [], {}
    unnamed = []
    for row in maps:
        role, extra = role_from_name(row['name'])
        facts = row['features']
        if role == 'normal' and not facts['normal_like'] and facts.get('normal_score', 0) < NAMED_NORMAL_SCORE:
            notes.append('named_normal_not_normal')
            unused.append(row['name'])
            continue
        if role in SCALAR and not facts['gray']:
            notes.append('named_scalar_is_colour')   # "Metal_Plate_2K": a material's name, not a metalness map.
            role = None
        if role is None and facts['normal_like']:
            role = 'normal'
        if role is None:
            unnamed.append(row)
            continue
        if role == 'packed':
            packed[row['name']] = PACKED[extra]
            continue
        claims.setdefault(role, []).append((row, extra))
    colour = [row for row in unnamed if not row['features']['gray']]
    if 'base_color' not in claims and len(colour) == 1:
        claims['base_color'] = [(colour[0], None)]
        notes.append('base_color_by_content')
        unnamed.remove(colour[0])
    unused.extend(row['name'] for row in unnamed)
    roles = {}
    for role, rows in claims.items():
        rows.sort(key=lambda pair: (-(pair[0].get('size') or 0), len(pair[0]['name']), pair[0]['name']))
        roles[role] = rows[0][0]['name']
        unused.extend(pair[0]['name'] for pair in rows[1:])
    convention = None
    if 'normal' in roles:
        row, named = next(pair for pair in claims['normal'] if pair[0]['name'] == roles['normal'])
        measured = normal_convention(row['pixels']) if row.get('pixels') is not None else None
        convention = measured or named or 'gl'
        if measured and named and measured != named:
            notes.append('normal_convention_from_pixels')
    if not roles and not packed:
        raise ValueError('none of the images is a recognisable material map')
    return {'roles': roles, 'packed': packed, 'normal': convention, 'unused': sorted(unused), 'notes': notes}
