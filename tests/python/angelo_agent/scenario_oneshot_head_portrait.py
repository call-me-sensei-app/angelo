# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-head-portrait``: a neutral head sculpted into a realistic person from pictures.

Both sides start from the same start file (``START``, appended into the
factory scene before the message): ``Head``, a neutral average head
(``head_designs.base``) with its eyes. The brief attaches three renders of
the ground truth (``head_designs.portrait``: an adult of average
proportions), states the orthographic pictures' scale and asks for the
person's likeness within 2 mm: the outline, eyes, brow, nose, lips, chin and
jaw line, the eyes moved into place. This is the likeness pass of a real
portrait sculpt, from a base mesh, so the precision of the sculpting itself
is what is measured.

The checks compare the side's head with the ground truth's in the pictures'
own views (``head_checks``, ``face_tests``); the truth is read from the
design itself (``face_truth``).
"""

import face_tests
import scenario_build_kit as kit

ID = "oneshot_head_portrait"
TITLE = "A neutral head sculpted into the person in the pictures"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "head-portrait-final.png", "size": face_tests.STILL_SIZE}
GROUND_TRUTH = "reference"
LIMITS = {"wall_s": 3000.0, "job_wait_s": 600.0, "cost_usd": 25.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]
SCALE, CENTRE_Z, TOLERANCE_MM = 0.512, -0.015, 2.0
START_DESIGN, START = "base", face_tests.START
START_CELL = 0.002
CAMERAS = face_tests.cameras(SCALE, CENTRE_Z, face_tests.HERO)
SUBJECT = face_tests.FACE_PARTS
FACE = {"design": "portrait", "head": [{"prefix": "Head"}], "eyes": {"L": face_tests.EYE_L, "R": face_tests.EYE_R},
        "hair": None}

PROMPT = (
    "The scene holds 'Head', a neutral head with its eyes, 'Head Eye L' and 'Head Eye R'. Sculpt it into the "
    "person in the attached pictures and render one still. The pictures show the finished head: an orthographic "
    "front view looking along +Y, an orthographic view of its right side looking along +X, and a three-quarter "
    "hero shot. This is a likeness: I will lay your head over the front and side pictures, so its outline, eyes, "
    "brow, nose, lips, chin and jaw line should each land within 2 mm of where they are in the pictures. Where "
    "the pictures and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The head faces -Y and is symmetric about x = 0. I find things by name, so keep "
    "these names and give anything else you add a meaningful name of its own. Keep the file tidy: nothing from "
    "the default scene (its cube, light or camera) remains, no unused leftovers, and a material on everything "
    "that shows.\n\n"
    "The pictures' scale. " + face_tests.frame_text(SCALE, CENTRE_Z) + "\n\n"
    "The head. 'Head' stays one mesh: the head and its neck, the neck cut flat at z = -0.2 m as it is now. Move "
    "the eyes, 'Head Eye L' (the head's left eye, on the +X side) and 'Head Eye R', to where the person's eyes "
    "are; they keep looking straight ahead. Nothing else has a name starting with 'Head'. Skin: a smooth, matte, "
    "natural light skin tone.\n\n"
    "Light. A light named 'Key Light' from the camera's left and above, and any other lights you like, so the "
    "forms read clearly.\n\n"
    "Camera. A camera named 'Hero Camera' at (0.62, -1.05, 0.1), looking at (0, 0, -0.02) with an 85 mm lens, "
    "as the scene's camera.\n\n"
    "Output. EEVEE, 1920 x 1080 at 100 %. Render the still from 'Hero Camera' and save it as a PNG named "
    "'head-portrait-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your head next to the pictures from the same views and fix what is wrong.")

CHECKS = face_tests.face_checks(ID, TOLERANCE_MM) + face_tests.scene_checks(face_tests.HERO, face_tests.STILL_SIZE)
TURNS = face_tests.turn(PROMPT, ["front", "side", "hero"], CHECKS, FINAL_STILL["name"], TITLE)


def make_start(bpy, folder):
    return face_tests.make_start(bpy, folder, START_DESIGN, START_CELL)


def setup(bpy, scene, path, sha256):
    return face_tests.setup(bpy, scene, path, sha256)


def build_turn(bpy, turn_id):
    """The perfect agent: the start head becomes the person, the eyes move; then the briefs' scene."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    kit.remove_default_objects(bpy)
    face_tests.replace_head(bpy, FACE["design"])
    face_tests.build_scene(bpy, face_tests.HERO, (0.0, 0.0, CENTRE_Z))


def build(bpy, upto=None):
    build_turn(bpy, "brief")
