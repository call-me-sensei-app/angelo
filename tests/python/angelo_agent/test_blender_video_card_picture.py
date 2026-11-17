# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's video card shows a clip as Blender shows it, whatever the clip's bit depth (BACKGROUND).

A ready video's card picture is its first frame (``studio_video.make_thumbnail``, ``preview.png``). Blender's movie
reader hands a clip of more than 8 bits a channel over as scene-linear floats and an 8-bit clip as bytes in the
clip's own colour space; a picture copied from either as if it were display bytes shows the wrong colours (found on
2026-11-14: every 10-bit clip's card came out far too dark, 0.50 shown as 0.21, while the clip played right).

The suite makes its own clips with Blender's video writer, from one flat colour, in each codec and bit depth the
build offers (a colour strip rendered by the Sequencer: no viewport, no GPU). For every clip it asks:

* what the reader says of it (bytes or floats, which colour space);
* the colour Blender itself shows for that file: the clip as a movie strip, rendered with the Standard view;
* the colour at the centre of the card picture ``make_thumbnail`` writes, and how deep that picture is.

The card must show what Blender shows, for every clip, in an 8-bit picture: every card's picture is display bytes
(the card and the team library's thumbnail read it so), and Blender reads a 16-bit PNG back as scene-linear floats,
so a deeper picture of the right colours is still a different kind of file per clip (2026-11-15: a float picture
saved by Blender came out 16 bits deep). At least one clip of each kind (bytes, floats) must have been made, or
the suite proves nothing. No provider, account, network or paid request; no data-block is left.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
import json
import traceback
from pathlib import Path

import bpy
import numpy

assert '--factory-startup' in sys.argv and bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
OUT = Path(os.environ['ANGELO_QA_DIR']).resolve()
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / 'state'), ANGELO_CONNECTIONS_DIR=str(OUT / 'accounts'),
                  ANGELO_DISABLE_DEFAULT_CHAT='1', ANGELO_DISABLE_KEYCHAIN='1')
from angelo_agent import studio_video  # noqa: E402
from angelo_agent.fal_video import VIDEO_FILE  # noqa: E402

REPORT = {'success': False, 'checks': {}, 'clips': {}, 'not_made': {}, 'paid_requests': 0,
          'binary': bpy.app.binary_path, 'module': studio_video.__file__}
COLOUR = (0.8, 0.5, 0.2)   # Display values: mid-range in every channel, where linear and display values differ most.
CLOSE = 0.04
# Every codec and depth Blender's writer may offer in an MP4; a pair this build does not have is recorded, not failed.
VARIANTS = tuple((codec, depth) for codec in ('H264', 'H265', 'AV1') for depth in ('8', '10', '12'))


def check(name, value):
    REPORT['checks'][name] = bool(value)
    return bool(value)


def id_counts():
    return {name: len(getattr(bpy.data, name)) for name in ('images', 'scenes', 'movieclips', 'sounds')}


def png_depth(path):
    """Bits a channel of a PNG file (its IHDR), or None when the file is not a PNG."""
    head = Path(path).read_bytes()[:26]
    return head[24] if head[:8] == b'\x89PNG\r\n\x1a\n' and head[12:16] == b'IHDR' else None


def centre(path):
    """The mean colour of the middle of an 8-bit picture file, as display values."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
        middle = values.reshape(height, width, 4)[height // 3:2 * height // 3, width // 3:2 * width // 3, :3]
        return [round(float(value), 4) for value in middle.reshape(-1, 3).mean(axis=0)]
    finally:
        bpy.data.images.remove(image)


def far(one, other):
    return max(abs(a - b) for a, b in zip(one, other))


def sequencer_scene(name):
    scene = bpy.data.scenes.new(name)
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 180, 100
    scene.view_settings.view_transform, scene.view_settings.look = 'Standard', 'None'
    scene.render.use_sequencer = True
    return scene, scene.sequence_editor_create()


def write_clip(path, codec, depth):
    """A four-frame clip of COLOUR; raises TypeError when this build's writer has no such codec or depth."""
    scene, editor = sequencer_scene('QA clip')
    try:
        strip = editor.strips.new_effect(name='colour', type='COLOR', channel=1, frame_start=1, length=4)
        strip.color = COLOUR
        scene.frame_start, scene.frame_end = 1, 4
        settings = scene.render.image_settings
        settings.media_type = 'VIDEO'
        settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format, scene.render.ffmpeg.codec = 'MPEG4', codec
        settings.color_depth = depth
        scene.render.ffmpeg.constant_rate_factor = 'HIGH'
        scene.render.filepath = str(path)
        scene.render.use_file_extension = False
        bpy.ops.render.render(animation=True, scene=scene.name)
    finally:
        bpy.data.scenes.remove(scene)
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('the writer left no file')


def blender_shows(path, picture):
    """The colour Blender shows for a video file: its first frame as a movie strip, in the Standard view."""
    scene, editor = sequencer_scene('QA strip')
    try:
        editor.strips.new_movie(name='clip', filepath=str(path), channel=1, frame_start=1)
        scene.frame_start = scene.frame_end = 1
        scene.render.image_settings.file_format = 'PNG'
        scene.render.filepath = str(picture)
        scene.render.use_file_extension = False
        bpy.ops.render.render(write_still=True, scene=scene.name)
    finally:
        bpy.data.scenes.remove(scene)
    return centre(picture)


class Library:
    """What ``make_thumbnail`` needs of the library: the checked file and the asset's update."""

    def __init__(self):
        self.updated = None

    def checked_file(self, asset, name):
        return Path(asset['folder']) / name

    def update(self, asset):
        self.updated = asset


def one_clip(codec, depth):
    name = '%s-%s' % (codec.lower(), depth)
    folder = OUT / name
    folder.mkdir(exist_ok=True)
    path = folder / VIDEO_FILE
    try:
        write_clip(path, codec, depth)
    except (TypeError, RuntimeError) as error:
        REPORT['not_made'][name] = str(error).strip().splitlines()[-1][:200]
        return
    row = REPORT['clips'][name] = {}
    movie = bpy.data.images.load(str(path), check_existing=False)
    row['reader'] = {'source': movie.source, 'is_float': movie.is_float, 'colorspace': movie.colorspace_settings.name}
    bpy.data.images.remove(movie)
    row['blender_shows'] = blender_shows(path, folder / 'strip.png')
    library = Library()
    asset = {'kind': 'VIDEO', 'status': 'ready', 'id': name, 'folder': str(folder)}
    row['made'] = studio_video.make_thumbnail(library, asset)
    card = folder / studio_video.THUMBNAIL
    row['card_shows'] = centre(card) if card.is_file() else None
    row['card_depth'] = png_depth(card) if card.is_file() else None
    check(name + '_blender_shows_the_clips_colour', far(row['blender_shows'], COLOUR) <= CLOSE)
    check(name + '_card_made', row['made'] is True and row['card_shows'] is not None
          and (library.updated or {}).get('thumbnail') == studio_video.THUMBNAIL)
    check(name + '_card_is_an_8_bit_picture', row['card_depth'] == 8)
    check(name + '_card_shows_what_blender_shows', row['card_depth'] == 8
          and row['card_shows'] is not None and far(row['card_shows'], row['blender_shows']) <= CLOSE)


def main():
    if not check('ffmpeg_built', bpy.app.ffmpeg.supported):
        return
    before = id_counts()
    for codec, depth in VARIANTS:
        one_clip(codec, depth)
    kinds = [row['reader']['is_float'] for row in REPORT['clips'].values()]
    check('an_8_bit_clip_was_made', False in kinds)
    check('a_high_bit_depth_clip_was_made', True in kinds)
    check('no_data_block_left', id_counts() == before)
    REPORT['success'] = all(REPORT['checks'].values())


try:
    main()
except Exception:
    REPORT['error'] = traceback.format_exc()
    raise
finally:
    (OUT / 'video-card-picture.json').write_text(json.dumps(REPORT, indent=1, default=str))
    print('ANGELO_VIDEO_CARD_PICTURE', json.dumps({'success': REPORT['success'], 'clips': len(REPORT['clips']),
                                                   'not_made': sorted(REPORT['not_made'])}), flush=True)
