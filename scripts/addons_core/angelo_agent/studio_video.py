# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's videos on the main thread: thumbnails, play, reveal, Sequencer; the resume pass is ``video_resume``.

``tick`` runs from ``studio.pump``: ``video_resume.tick`` resumes saved fal requests and gives a ready video its
``preview.png`` from the first frame (Blender's movie reader, ``make_thumbnail``), once. A video Blender can't read
(a build without FFmpeg) keeps its card text; the video itself is untouched.

Play opens the video in the animation player (this executable with ``-a``: play, pause, scrub, loop), the player
*View Animation* uses. Add to Sequencer puts a movie strip (with its sound, when the file has
one) at the current frame on the first free channels, as one Undo step; dragging the card onto the Sequencer does
the same through Blender's own file drop (``studio_drag``).
"""

import subprocess
import sys

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import video_resume
from .fal_video import VIDEO_FILE
from .ui_text import iface_, n_, rpt_

THUMBNAIL = "preview.png"
THUMBNAIL_EDGE = 768


def video_path(library, asset):
    """The ready video's checked file (``StudioLibrary.checked_file``: no symbolic link, no other folder)."""
    if asset.get("kind") != "VIDEO" or asset.get("status") != "ready":
        raise ValueError(n_("This video isn't ready yet"))
    return library.checked_file(asset, VIDEO_FILE)


def tick(library, jobs, bind):
    """Resume and thumbnail work for the library (``video_resume.tick``, with this module's thumbnails)."""
    return video_resume.tick(library, jobs, bind, make_thumbnail)


def make_thumbnail(library, asset):
    """``preview.png`` from the video's first frame; False (and nothing written) when Blender can't read it."""
    movie = picture = None
    try:
        path = video_path(library, asset)
        movie = bpy.data.images.load(str(path), check_existing=False)
        width, height = movie.size
        if movie.source != 'MOVIE' or not width or not height or width * height > 64 * 1024 * 1024:
            return False
        if max(width, height) > THUMBNAIL_EDGE:   # Scaled first: the copy below is of the small frame.
            factor = THUMBNAIL_EDGE / max(width, height)
            movie.scale(max(1, int(width * factor)), max(1, int(height * factor)))
            width, height = movie.size
        picture = bpy.data.images.new("Angelo video preview", width, height)   # i18n: a private, removed data-block name
        picture.pixels.foreach_set(_pixels(movie))
        picture.filepath_raw = str(path.with_name(THUMBNAIL))
        picture.file_format = 'PNG'
        picture.save()
        library.update(dict(asset, thumbnail=THUMBNAIL))
        return True
    except (ValueError, OSError, RuntimeError):
        return False
    finally:
        for image in (movie, picture):
            if image is not None:
                bpy.data.images.remove(image)


def _pixels(image):
    import numpy
    values = numpy.empty(image.size[0] * image.size[1] * 4, dtype=numpy.float32)
    image.pixels.foreach_get(values)
    return values


def play(library, asset):
    """Open the video in Blender's animation player, a separate process of this executable."""
    path = video_path(library, asset)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
    subprocess.Popen([bpy.app.binary_path, "-a", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **flags)


def reveal(library, asset):
    """Show the video's file in Finder or Explorer."""
    path = video_path(library, asset)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        bpy.ops.wm.path_open(filepath=str(path.parent))


def add_to_sequencer(scene, library, assets):
    """Movie strips for ``assets`` at the current frame, one after another on the first channels free there."""
    editor = scene.sequence_editor or scene.sequence_editor_create()
    frame, added = scene.frame_current, []
    for asset in assets:
        path = video_path(library, asset)
        channel = _free_channel(editor, frame)
        strip = editor.strips.new_movie(name=(asset.get("prompt") or "Video")[:60], filepath=str(path),
                                        channel=channel, frame_start=frame)
        try:
            editor.strips.new_sound(name=strip.name + " audio", filepath=str(path), channel=channel + 1, frame_start=frame)
        except RuntimeError:
            pass   # A video without a sound track.
        added.append(strip)
        frame = int(strip.frame_final_end)
    return added


def _free_channel(editor, frame):
    used = {s.channel for s in editor.strips_all if s.frame_final_start <= frame < s.frame_final_end}
    return next(c for c in range(1, 127) if c not in used and c + 1 not in used)


def _ids(text):
    return [p.strip() for p in text.replace(",", "\n").splitlines() if p.strip()]


class ANGELO_OT_studio_video(Operator):
    bl_idname = "angelo.studio_video"
    bl_label = "Media Studio Video"
    bl_description = "Play a Media Studio video, show its file, or add it to the Sequencer"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=[("PLAY", "Play", "Play in the animation player"),
                                ("SEQUENCER", "Add to Sequencer", "Add as movie strips at the current frame"),
                                ("REVEAL", "Show File", "Show the video file in Finder or Explorer")])
    item_ids: StringProperty(name="Media Studio items", description="Media Studio ids, one per line or comma")

    def execute(self, context):
        from . import studio
        library = studio.library()
        assets = [library.assets[i] for i in _ids(self.item_ids) if library.assets.get(i, {}).get("kind") == "VIDEO"]
        try:
            if not assets:
                raise ValueError(n_("Choose a ready video"))
            if self.action == "SEQUENCER":
                added = add_to_sequencer(context.scene, library, assets)
                self.report({'INFO'}, rpt_("Added {count} video strips to the Sequencer", count=len(added)))
            elif self.action == "PLAY":
                play(library, assets[0])
            else:
                reveal(library, assets[0])
            return {'FINISHED'}
        except (ValueError, OSError, RuntimeError, StopIteration) as error:
            from .studio_state import error_text
            self.report({'ERROR'}, error_text(error))
            return {'CANCELLED'}


def draw_actions(layout, identities):
    """A ready video's menu items (the grid's right-click menu and the item popup)."""
    identities = "\n".join(identities)
    row = layout.row()
    for action, icon in (("PLAY", 'PLAY'), ("SEQUENCER", 'SEQUENCE'), ("REVEAL", 'FILE_FOLDER')):
        op = row.operator(ANGELO_OT_studio_video.bl_idname, text=iface_(_LABELS[action]), icon=icon, translate=False)
        op.action, op.item_ids = action, identities


_LABELS = {"PLAY": n_("Play"), "SEQUENCER": n_("Add to Sequencer"), "REVEAL": n_("Show File")}
CLASSES = (ANGELO_OT_studio_video,)
