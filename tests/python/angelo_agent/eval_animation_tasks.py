# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The coverage set's animation editing tasks, split from eval_live_tasks.py to keep it small.

Graded like the rest of the set by closed checks on ``$eval`` (the eval cube),
``$copy`` (its duplicate) and ``$scene``; test_blender_coverage_tasks.py builds
each end state with plain ``bpy`` and proves each fails before its edit.
"""


def animation_coverage_tasks(quoted, copy_quoted):
    """Animation editing: one task per mechanism, each graded by closed checks on the stored result.

    keyframe_configure (extrapolation), keyframe_edit (a time offset), fcurve_modifier, keyframe_copy,
    action_set (a new Action with the old one stashed), nla_edit (a track and a strip of another
    object's Action) and marker_edit, in that order on the eval cube, its copy and the scene.
    """
    return [
        {"id": "key_linear", "kind": "edit", "requires_object": True,
         "prompt": ("Animate %s's location: (0, 0, 0) at frame 1 and (4, 0, 0) at frame 30. Make those keys linear, "
                    "with linear extrapolation.") % quoted,
         "checks": [{"kind": "keyframes", "target": "$eval", "property": "location", "index": 0, "frames": [1, 30],
                     "interpolation": "LINEAR", "extrapolation": "LINEAR"}],
         "source": "native coverage: keyframe_insert, keyframe_configure (extrapolation)"},
        {"id": "key_retime", "kind": "edit", "requires_object": True,
         "prompt": "Shift all of %s's location keys 10 frames later." % quoted,
         "checks": [{"kind": "keyframes", "target": "$eval", "property": "location", "index": 0, "frames": [11, 40],
                     "absent": [1, 30]}],
         "source": "native coverage: keyframe_edit (OFFSET)"},
        {"id": "key_noise", "kind": "edit", "requires_object": True,
         "prompt": ("Add a Noise F-Curve modifier named 'Eval Shake' with a strength of 0.5 to %s's X location "
                    "animation channel.") % quoted,
         "checks": [{"kind": "fcurve_modifier", "target": "$eval", "property": "location", "index": 0,
                     "name": "Eval Shake", "values": {"strength": 0.5}, "tolerance": 0.0001}],
         "source": "native coverage: fcurve_modifier"},
        {"id": "copy_keys", "kind": "edit", "requires_object": True,
         "prompt": "Copy %s's X location keys onto %s, 5 frames later." % (quoted, copy_quoted),
         "checks": [{"kind": "keyframes", "target": "$copy", "property": "location", "index": 0, "frames": [16, 45]}],
         "source": "native coverage: keyframe_copy"},
        {"id": "stash_action", "kind": "edit", "requires_object": True,
         "prompt": "Give %s a new empty action named 'Eval Idle', stashing its current action in the NLA." % copy_quoted,
         "checks": [{"kind": "nla", "target": "$copy", "action": "Eval Idle", "minimum_tracks": 1}],
         "source": "native coverage: action_set (NEW, stash_previous)"},
        {"id": "nla_strip", "kind": "edit", "requires_object": True,
         "prompt": ("On %s, add an NLA track named 'Eval Layer' holding a strip named 'Eval Walk' that plays %s's "
                    "action from frame 50, repeated twice and blended with Add.") % (copy_quoted, quoted),
         "checks": [{"kind": "nla", "target": "$copy", "track_names": ["Eval Layer"], "strip": {
             "track": "Eval Layer", "name": "Eval Walk", "values": {"frame_start": 50, "repeat": 2, "blend_type": "ADD"}}}],
         "source": "native coverage: nla_edit (TRACK_ADD, STRIP_ADD)"},
        {"id": "timeline_markers", "kind": "edit", "requires_object": False,
         "prompt": "Add timeline markers named 'Eval Start' at frame 1 and 'Eval Hit' at frame 24.",
         "checks": [{"kind": "markers", "target": "$scene", "present": [{"name": "Eval Start", "frame": 1},
                                                                        {"name": "Eval Hit", "frame": 24}]}],
         "source": "native coverage: marker_edit"},
    ]
