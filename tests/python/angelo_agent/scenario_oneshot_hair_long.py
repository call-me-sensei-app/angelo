# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-hair-long``: long hair grown on a given head to match pictures.

Both sides start from the same start file (``START``): ``Head``, the
portrait test's person (``head_designs.portrait``) with its eyes. The brief
attaches four renders of the ground truth (``hair_kit`` style ``long``: straight
hair parted in the middle, falling over the ears and beside the face to about
22 cm below the eyes, as real hair strands), states the orthographic pictures'
scale and asks for
real strands rooted on the scalp, the hair's outline within 4 mm, the
hairline over the forehead within 3 mm and the length within 1 cm, leaving
the head untouched.

The checks read the hair however it is made: hair curves (with their own
Geometry Nodes), a hair particle system or hair meshes (``face_casters``,
``head_checks``); the truth is read from the design and the style
(``face_truth``).
"""

import face_tests
import hair_kit
import head_designs
import scenario_build_kit as kit

ID = "oneshot_hair_long"
TITLE = "Long straight hair parted in the middle, on a given head"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "hair-long-final.png", "size": face_tests.STILL_SIZE}
GROUND_TRUTH = "reference"
LIMITS = {"wall_s": 3000.0, "job_wait_s": 600.0, "cost_usd": 25.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]
SCALE, CENTRE_Z = 0.704, -0.06
START_DESIGN, START = "portrait", face_tests.START
START_CELL = 0.0016
HERO = face_tests.HAIR_HERO
CAMERAS = face_tests.cameras(SCALE, CENTRE_Z, HERO, back=True)
SUBJECT = face_tests.FACE_PARTS + ({"prefix": "Hair"},)
FACE = {"design": "portrait", "head": [{"prefix": "Head"}], "eyes": {"L": face_tests.EYE_L, "R": face_tests.EYE_R},
        "hair": {"style": "long", "target": {"prefix": "Hair"}}}
STYLE = ("long, straight hair parted in the middle: it falls from the parting over the ears and beside the face, "
         "never across it, and ends level about 22 cm below the eyes, at the back and at the sides alike")

PROMPT = (
    "The scene holds 'Head', a person's head with its eyes, 'Head Eye L' and 'Head Eye R'. Give it the hairstyle in "
    "the attached pictures and render one still. The pictures show the finished hair: an orthographic front view "
    "looking along +Y, an orthographic view of the head's right side looking along +X, an orthographic back "
    "view looking along -Y, and a three-quarter hero shot. It is " + STYLE + ". I will lay your result over the "
    "front, side and back pictures, so the hair's outline should land within 4 mm of the pictures', its hairline "
    "over the forehead within 3 mm, and its length at the back and the sides within 1 cm. Where the pictures and "
    "this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The head faces -Y. I find things by name, so keep the head's names and give "
    "anything else you add a meaningful name of its own. Keep the file tidy: nothing from the default scene (its "
    "cube, light or camera) remains, no unused leftovers, and a material on everything that shows.\n\n"
    "The pictures' scale. " + face_tests.frame_text(SCALE, CENTRE_Z).replace(
        "front view and", "front and back views and") + "\n\n"
    "The hair. Real hair: individual strands, ten thousand at least, each rooted on the scalp and never inside "
    "the head, dark brown. Every object of the hair has a name starting with 'Hair'. Leave 'Head' and its eyes as "
    "they are.\n\n"
    "Light. A light named 'Key Light' from the camera's left and above, and any other lights you like.\n\n"
    "Camera. A camera named 'Hero Camera' at (0.72, -1.2, 0.16), looking at (0, 0, -0.05) with a 70 mm lens, as "
    "the scene's camera.\n\n"
    "Output. EEVEE, 1920 x 1080 at 100 %. Render the still from 'Hero Camera' and save it as a PNG named "
    "'hair-long-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at the hair next to the pictures from the same views and fix what is wrong.")

CHECKS = (face_tests.hair_checks(ID, outline_mm=4.0, hairline_mm=3.0, length_mm=10.0, min_iou=0.85, min_strands=10000)
          + face_tests.scene_checks(HERO, face_tests.STILL_SIZE))
TURNS = face_tests.turn(PROMPT, ["front", "side", "back", "hero"], CHECKS, FINAL_STILL["name"], TITLE)


def make_start(bpy, folder):
    return face_tests.make_start(bpy, folder, START_DESIGN, START_CELL)


def setup(bpy, scene, path, sha256):
    return face_tests.setup(bpy, scene, path, sha256)


def build_turn(bpy, turn_id):
    """The perfect agent: the style's strands as one hair curves object on the head; then the briefs' scene."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    kit.remove_default_objects(bpy)
    hair_kit.build_hair(bpy, head_designs.design(FACE["design"]), FACE["hair"]["style"],
                        surface=bpy.data.objects["Head"])
    face_tests.build_scene(bpy, HERO, (0.0, 0.0, CENTRE_Z))


def build(bpy, upto=None):
    build_turn(bpy, "brief")
