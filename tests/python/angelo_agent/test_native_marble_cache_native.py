# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native adapter for the additive cache contract; no timers, screenshots or additional provider round."""

import copy
import hashlib
import struct
import time
import zlib

import test_native_marble_cache_probe as probe


def png(path, rgb):
    """Write captured framebuffer bytes without creating Blender image IDs or causing another redraw."""
    height, width, _ = rgb.shape
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    rows = b''.join(b'\0' + row.tobytes() for row in rgb[::-1])
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    assert path.is_file() and path.stat().st_size > 0
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def run(window, area, region, controller, silent_deadline, out):
    import bpy
    import gpu
    import numpy as np
    from angelo_agent import chat_view, editor_types, marble_frames, marble_host, marble_wait, ui, live_windows

    probe.assert_inactive()
    start = time.monotonic()
    hard_deadline = min(start + probe.MAX_SECONDS, silent_deadline)
    pointers = tuple(item.as_pointer() for item in (window, area, region))
    original_wait = marble_host.tracker.current(controller)
    session = controller.state['id']
    space = area.spaces.active
    state = chat_view._views[area.as_pointer()]
    original_handler = chat_view._handler
    selection_keys = ('selection', 'focus', 'focused', 'input_serial', 'focus_request', 'blur_request')
    closed = [True]

    def alive():
        return any(w.as_pointer() == pointers[0] and any(
            a.as_pointer() == pointers[1] and any(r.as_pointer() == pointers[2] for r in a.regions)
            for a in live_windows.areas(w)) for w in live_windows.windows(bpy.context.window_manager))

    def identity():
        return (alive() and ui._controller is controller and controller.state['id'] == session
                and chat_view._views.get(pointers[1]) is state and area.spaces.active == space)

    def quiet():
        return (identity() and original_wait is not None and controller.busy
                and marble_host.tracker.current(controller) is original_wait
                and original_wait.phase == marble_wait.CARVING)

    def target():
        context = bpy.context
        return all(item is not None and item.as_pointer() == pointer
                   for item, pointer in zip((context.window, context.area, context.region), pointers))

    def read_draft():
        assert identity(), 'Original draft owner changed'
        draft = editor_types.read_chat_draft(space)
        assert draft is not None, 'No accessible native draft'
        return draft

    def write_draft(draft):
        assert identity(), 'Original draft owner changed'
        editor_types.write_chat_draft(space, draft)
        assert read_draft() == draft, 'Native draft write did not apply'

    def read_selection():
        assert identity(), 'Original selection owner changed'
        return {key: (key in state, copy.deepcopy(state.get(key))) for key in selection_keys}

    def restore_selection(saved):
        assert identity(), 'Original selection owner changed'
        for key, (present, value) in saved.items():
            if present:
                state[key] = copy.deepcopy(value)
            else:
                state.pop(key, None)

    observer = probe.Observer(marble_frames, marble_host, chat_view, area, region, target)

    def post():
        if closed[0] or not target():
            return
        observer.post()
        record = observer.record
        if record is None:
            return
        try:
            assert chat_view._handler is original_handler, 'Product draw handler changed'
            assert record['reuse_calls'] == 1, 'Capture ran before the product draw or after multiple draws'
            if not record['capture']:
                return
            assert record['post_calls'] == 1, 'Duplicate pixel capture'
            framebuffer = gpu.state.active_framebuffer_get()
            viewport = tuple(framebuffer.viewport_get())
            width, height = region.width, region.height
            assert viewport == (0, 0, width, height), 'Capture is not the target region framebuffer'
            pixels = framebuffer.read_color(0, 0, width, height, 4, 0, 'UBYTE')
            pixels.dimensions = width * height * 4
            rgb = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
            # A bypass draw has no cache key call. Read the same pure key after that actual draw;
            # do not alter the reuse decision or call any additional draw/timer callback.
            key = record.get('draw_key')
            if key is None and record['bypass']:
                key = observer.original_key(area, region, controller, state, original_wait, time.monotonic())
            record.update(pixels=rgb, content_key=copy.deepcopy(key), size=(width, height),
                          capture_sequence=record['sequence'], viewport=viewport,
                          holes=[tuple(item['rect']) for item in state.get('marble_drawn', {}).values()],
                          pixels_sha256=hashlib.sha256(rgb.tobytes()).hexdigest())
        except Exception:
            import traceback
            record['errors'].append(traceback.format_exc())

    def install():
        assert original_handler is not None and chat_view._handler is original_handler
        handle = bpy.types.SpaceConsole.draw_handler_add(post, (), 'WINDOW', 'POST_PIXEL')
        closed[0] = False
        return handle

    def remove(handle):
        closed[0] = True  # A failing removal still leaves an inert callback and fails cleanup.
        bpy.types.SpaceConsole.draw_handler_remove(handle, 'WINDOW')

    def paint():
        with bpy.context.temp_override(window=window, area=area, region=region):
            return bpy.ops.wm.redraw_timer(type='DRAW_SWAP', iterations=1)

    def save(first, direct):
        return [png(out / ('cache-contract-' + name + '.png'), record['pixels'])
                for name, record in (('reused', first), ('direct', direct))]

    driver = probe.Driver(observer, paint, quiet, read_draft, write_draft,
                          hard_deadline - .25, alive=alive)
    report = probe.execute(driver, install, remove, read_selection, restore_selection, save, hard_deadline)
    report.update(case_budget_seconds=probe.MAX_SECONDS, silent_deadline=silent_deadline,
                  metadata_cases=['size', 'expiry'], physical_resize_tested=False,
                  target=list(pointers), capture_order='POST_PIXEL after existing product handler')
    return report
