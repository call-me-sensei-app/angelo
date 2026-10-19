# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scene settings a background job changes for itself, and their restore.

A job's own resolution, samples, image format, output path, engine (texture
bake) or frame range (USD) are set on the scene only while it runs. The values
they replaced are recorded on the scene first, as the ``_angelo_job_restore``
custom property (JSON: ``{"job": id, "values": {path: original}, "extras":
{key: data}}``), so:

* the job restores them when it ends (``Overrides.restore``), whatever its outcome;
* a file saved while the job ran is restored when it is next opened, or when
  Angelo next starts after a crash (``restore_tags``, from ``job_ui``);
* a request admitted while another job's values are applied reads the
  person's own settings, not the running job's (``scene_value``).

Paths are RNA paths from the scene: dotted settings (``render.resolution_x``,
``cycles.samples``) and items found by name
(``compositing_node_group.nodes["File Output"].directory``). A setting can
constrain another (an image format limits its colour depth and its view
format), so a restore repeats until every recorded value is back and returns
the paths Blender did not take. ``extras`` are a runner's other temporary
changes as plain data, each put back by its own module (``EXTRA_RESTORERS``).
Animation on the scene that writes a recorded setting is muted for the job
(``job_animated_settings``; extras key ``held_channels``) so the job's value is
what renders on every frame, and unmuted with the restore.
Main thread only.
"""

import json

import bpy

from . import job_animated_settings as animated

TAG = "_angelo_job_restore"


def _restore_bake_nodes(data):
    from . import job_bake_targets
    job_bake_targets.restore(data)


def _remove_bake_image(data):
    from . import job_bake_targets
    job_bake_targets.remove_created(data)


# A runner's temporary changes beyond scene settings: recorded key -> the function that puts its data back.
EXTRA_RESTORERS = {"bake_nodes": _restore_bake_nodes, "bake_image": _remove_bake_image}
RESTORE_PASSES = 3


def _owner(scene, path):
    """The RNA struct that owns the last attribute of ``path``, and that attribute's name."""
    head, _, name = path.rpartition(".")
    if "[" in head:
        return scene.path_resolve(head), name  # Items by name or index, as RNA resolves them.
    owner = scene
    for part in head.split(".") if head else ():
        owner = getattr(owner, part)
    return owner, name


def get(scene, path):
    return getattr(*_owner(scene, path))


def put(scene, path, value):
    owner, name = _owner(scene, path)
    setattr(owner, name, value)


def rna_key(scene, path):
    """``(struct identifier, property)`` that writing ``path`` changes: the key ``bpy.msgbus`` publishes it to."""
    owner, name = _owner(scene, path)
    return owner.bl_rna.identifier, name


def _tag(scene):
    """What a job recorded on this scene: ``(values, extras)``, each {} when none or unreadable."""
    raw = scene.get(TAG)
    try:
        data = json.loads(raw) if raw is not None else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    values, extras = data.get("values"), data.get("extras")
    return (values if isinstance(values, dict) else {}), (extras if isinstance(extras, dict) else {})


def _recorded(scene):
    """``{path: original}`` a job recorded on this scene ({} when none or unreadable)."""
    return _tag(scene)[0]


def _differs(scene, path, value):
    try:
        return get(scene, path) != value
    except (AttributeError, TypeError, ValueError, ReferenceError, KeyError):
        return True  # No longer there (a renamed node, a removed object): reported as not restored.


def put_back(scene, originals):
    """Set every recorded value again, last applied first; returns the paths that did not take their value."""
    pending = [(path, value) for path, value in reversed(list(originals.items()))]
    for _attempt in range(RESTORE_PASSES):
        for path, value in pending:
            try:
                if get(scene, path) != value:
                    put(scene, path, value)
            except (AttributeError, TypeError, ValueError, ReferenceError, KeyError):
                pass  # A later pass may succeed once the setting it depends on is back.
        pending = [(path, value) for path, value in pending if _differs(scene, path, value)]
        if not pending:
            break
    return [path for path, _value in pending]


def _restore_extras(extras):
    for key, data in extras.items():
        restorer = EXTRA_RESTORERS.get(key)
        if restorer is not None:
            try:
                restorer(data)
            except Exception:
                pass  # One runner's leftovers never stop the others being put back.


def scene_value(scene, path):
    """The person's own value of a setting, even while a running job has replaced it."""
    recorded = _recorded(scene)
    return recorded[path] if path in recorded else get(scene, path)


def restore_tags(busy_scenes=()):
    """Restore what a job left on a saved scene; ``busy_scenes`` (session UIDs) still have a running job."""
    restored = []
    for scene in bpy.data.scenes:
        if TAG not in scene or scene.session_uid in busy_scenes:
            continue
        values, extras = _tag(scene)
        put_back(scene, values)  # A setting this build no longer has is skipped; the rest are restored.
        animated.release(scene, extras.get(animated.HELD))
        _restore_extras(extras)
        del scene[TAG]
        restored.append(scene.name)
    return restored


class Overrides:
    """Settings applied for one job and restored when it ends, recorded on the scene meanwhile."""

    def __init__(self, scene, identity):
        self.scene, self.identity, self.originals, self.extras = scene, identity, {}, {}

    def _record(self):
        self.scene[TAG] = json.dumps({"job": self.identity, "values": self.originals, "extras": self.extras})

    def apply(self, values, also_record=()):
        """Record the originals, then set ``values`` in order. ``also_record`` are settings the job does not set
        itself but one of its values changes (a file format coerces the colour depth): restored with the rest."""
        for path in (*also_record, *values):
            self.originals.setdefault(path, get(self.scene, path))
        self.hold((*also_record, *values))
        for path, value in values.items():
            put(self.scene, path, value)

    def hold(self, paths):
        """Mute the scene's animation of ``paths`` for the job (recorded first, so a saved file gets it back)."""
        held = self.extras.setdefault(animated.HELD, [])
        held += animated.hold(self.scene, paths)
        self._record()

    def release(self):
        """Unmute what ``hold`` muted; the paths of channels no longer found."""
        try:
            return animated.release(self.scene, self.extras.pop(animated.HELD, None))
        except ReferenceError:
            return []  # The scene was removed while the job ran.

    def note(self, key, data):
        """Record a runner's other temporary change (JSON data its ``EXTRA_RESTORERS`` entry can put back)."""
        self.extras[key] = data
        self._record()

    def restore(self):
        """Put everything back; returns the setting paths Blender did not take (normally none)."""
        unrestored = []
        try:
            unrestored = put_back(self.scene, self.originals) + self.release()
            if TAG in self.scene:
                del self.scene[TAG]
        except ReferenceError:
            pass  # The scene was removed while the job ran; nothing is left to restore.
        self.originals, self.extras = {}, {}
        return unrestored


class DisplayNone:
    """Keep the artist's layout: a job's render never opens a render view (restored at once)."""

    def __enter__(self):
        prefs = bpy.context.preferences
        self.view, self.dirty = prefs.view.render_display_type, prefs.is_dirty
        prefs.view.render_display_type = 'NONE'

    def __exit__(self, *_):
        prefs = bpy.context.preferences
        prefs.view.render_display_type = self.view
        if not prefs.bl_rna.properties['is_dirty'].is_readonly:
            prefs.is_dirty = self.dirty
