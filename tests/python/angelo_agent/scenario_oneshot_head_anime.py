# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-head-anime``: an anime head sculpted to front and side pictures.

The brief attaches three renders of the ground truth (``head_designs.anime``:
very large eyes set low, a small pointed nose, a small mouth and a narrow face tapering to a pointed chin):
an orthographic front view, an orthographic view of the head's right side and
a three-quarter hero shot. It states the orthographic pictures' scale and
centre, as a reference sheet would, so every feature can be placed to the
millimetre, and asks for the outline, eyes, nose, mouth, chin and jaw line
within 2 mm of the pictures. The head is made from nothing: the route is
what is measured.

The checks compare the side's head with the ground truth's in the pictures'
own views (``head_checks``, ``face_tests``); the truth is read from the
design itself (``face_truth``).
"""

import face_tests
import head_kit
import scenario_build_kit as kit

ID = "oneshot_head_anime"
TITLE = "An anime head, sculpted to front and side pictures"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "head-anime-final.png", "size": face_tests.STILL_SIZE}
GROUND_TRUTH = "reference"
LIMITS = {"wall_s": 2400.0, "job_wait_s": 600.0, "cost_usd": 20.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]
SCALE, CENTRE_Z, TOLERANCE_MM = 0.512, -0.015, 2.0
CAMERAS = face_tests.cameras(SCALE, CENTRE_Z, face_tests.HERO)
SUBJECT = face_tests.FACE_PARTS
FACE = {"design": "anime", "head": [{"prefix": "Head"}], "eyes": {"L": face_tests.EYE_L, "R": face_tests.EYE_R},
        "hair": None}

PROMPT = (
    "Sculpt the anime-style head in the attached pictures and render one still of it. The pictures show the "
    "finished head: an orthographic front view looking along +Y, an orthographic view of its right side looking "
    "along +X, and a three-quarter hero shot. Match the front and side pictures as closely as you can: I will lay "
    "your head over them, so its outline, eyes, nose, mouth, chin and jaw line should each land within 2 mm of "
    "where they are in the pictures. Where the pictures and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The head faces -Y and is symmetric about x = 0. I find things by name, so name "
    "them exactly as written here and give anything else you add a meaningful name of its own. Keep the file "
    "tidy: nothing from the default scene (its cube, light or camera) remains, no unused leftovers, and a "
    "material on everything that shows.\n\n"
    "The pictures' scale. " + face_tests.frame_text(SCALE, CENTRE_Z) + "\n\n"
    "The head. One mesh named 'Head': the head and its neck, the neck cut flat at z = -0.2 m. The eyes are two "
    "separate objects named 'Head Eye L' (the head's left eye, on the +X side) and 'Head Eye R', large and "
    "shaped as in the pictures, set in their sockets, with a large brown iris and a black pupil, looking straight "
    "ahead. Nothing else has a name "
    "starting with 'Head'. Skin: smooth, matte and pale.\n\n"
    "Light. A light named 'Key Light' from the camera's left and above, and any other lights you like, so the "
    "forms read clearly.\n\n"
    "Camera. A camera named 'Hero Camera' at (0.62, -1.05, 0.1), looking at (0, 0, -0.02) with an 85 mm lens, "
    "as the scene's camera.\n\n"
    "Output. EEVEE, 1920 x 1080 at 100 %. Render the still from 'Hero Camera' and save it as a PNG named "
    "'head-anime-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your head next to the pictures from the same views and fix what is wrong.")

CHECKS = face_tests.face_checks(ID, TOLERANCE_MM) + face_tests.scene_checks(face_tests.HERO, face_tests.STILL_SIZE)
TURNS = face_tests.turn(PROMPT, ["front", "side", "hero"], CHECKS, FINAL_STILL["name"], TITLE)


def build_turn(bpy, turn_id):
    """The ground truth: the design's head and eyes, the briefs' lights, world, camera and render settings."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    kit.remove_default_objects(bpy)
    head_kit.build_head(bpy, FACE["design"])
    face_tests.build_scene(bpy, face_tests.HERO, (0.0, 0.0, CENTRE_Z))


def build(bpy, upto=None):
    build_turn(bpy, "brief")
