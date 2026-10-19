# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded preview pixels: main-thread pump decodes, draw only uploads/renders.

Private observation UUIDs are immutable. A failed decode is cached until its
preview leaves the live set or lifecycle invalidation clears it, avoiding redraw
I/O/retry loops. Six entries bound CPU pixels and GPU textures together.
"""

from array import array
from collections.abc import Mapping
import threading

CACHE_LIMIT = 6
PIXEL_LIMIT = 768 * 768
_entries = {}
_shader = None


def cache_key(descriptor, controller):
    changes = descriptor.get('changes', {})
    views = changes.get('views', []) if isinstance(changes, Mapping) else []
    reference = views[0] if views and isinstance(views[0], Mapping) else None
    return (controller.state['id'], reference.get('id')) if reference and reference.get('id') else None


def prepared(descriptor, controller):
    """Pure cache inspection; never load pixels or inspect Blender datablocks."""
    return _entries.get(cache_key(descriptor, controller)) is not None


def _decode(controller, descriptor):
    import bpy
    image = None
    try:
        reference = descriptor['changes']['views'][0]
        path = controller.references.checked_path(reference)
        image = bpy.data.images.load(str(path), check_existing=False)
        image.colorspace_settings.name = 'Non-Color'
        width, height = image.size
        if not 0 < width * height <= PIXEL_LIMIT:
            raise ValueError('Native preview exceeds the bounded pixel budget')
        pixels = array('f', [0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        return {'size': (width, height), 'pixels': pixels, 'texture': None}
    finally:
        if image is not None:
            bpy.data.images.remove(image)


def prepare(controllers, *, limit=1):
    """Decode at most one private image per UI-pump tick, never on a worker.

    Controllers are ordered active-first. Inspect only their bounded retained
    preview inventory; decoding/removing the temporary Image completes before
    Blender can process a save, snapshot or another timer.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Native preview pixels must be prepared on the main thread')
    requests = {}
    for controller in controllers:
        for descriptor in reversed(controller.native_previews()):
            key = cache_key(descriptor, controller)
            if key is not None and key not in requests and len(requests) < CACHE_LIMIT:
                requests[key] = (controller, descriptor)
    for key in _entries.keys() - requests.keys():
        _entries.pop(key)
    count = 0
    for key, (controller, descriptor) in requests.items():
        if key in _entries or count >= limit:
            continue
        try:
            _entries[key] = _decode(controller, descriptor)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            _entries[key] = None
        controller.version += 1
        count += 1
    return count


def draw(descriptor, rect, controller):
    """GPU-context-only upload and draw from previously prepared CPU pixels."""
    global _shader
    import gpu
    from gpu_extras.batch import batch_for_shader
    entry = _entries.get(cache_key(descriptor, controller))
    if entry is None:
        return False
    width, height = entry['size']
    texture = entry['texture']
    if texture is None:
        texture = entry['texture'] = gpu.types.GPUTexture((width, height), format='RGBA16F',
            data=gpu.types.Buffer('FLOAT', [width * height * 4], entry['pixels']))
    x, y, w, h = rect
    scale = min(w / width, h / height)
    iw, ih = width * scale, height * scale
    x, y = x + (w - iw) / 2, y + (h - ih) / 2
    if _shader is None:
        _shader = gpu.shader.from_builtin('IMAGE')
    from .canvas_rtl import upright
    with upright(x, iw) as x:  # The chat may be mirrored (right-to-left language); a picture never is.
        batch = batch_for_shader(_shader, 'TRIS', {'pos': [(x, y), (x + iw, y), (x + iw, y + ih), (x, y + ih)],
            'texCoord': [(0, 0), (1, 0), (1, 1), (0, 1)]}, indices=[(0, 1, 2), (0, 2, 3)])
        _shader.bind()
        _shader.uniform_sampler('image', texture)
        batch.draw(_shader)
    return True


def clear():
    """Release both caches on document load, Undo/Redo and addon unregister."""
    global _shader
    _entries.clear()
    _shader = None
