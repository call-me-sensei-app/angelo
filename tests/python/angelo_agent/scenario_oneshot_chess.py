# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-chess``: a whole chess set on its board, in its starting position, from one message.

The parallel-agents benchmark (the parallel-agents design, "Benchmark"): the same brief and pictures go to a
single Angelo chat and to Angelo with parallel agents on, and only that switch differs. A chess set is a job a
person splits by unique design (a board and six piece designs, each repeated); the brief says nothing about how to
split it, and nothing in Angelo knows it is chess.

The brief attaches four pictures rendered from the stand-in (``parallel_kits.build_chess``): the hero shot, the
board from above, White's back rank and a white knight in profile. The checks need only the board's name: a ray straight down through
every square's centre finds the right piece's height on the 32 starting squares and nothing above the board on the
32 empty ones, so they hold however the pieces are made, named, copied or merged. How each piece class and the board
look is graded per class by the vision review (``bench_parallel_grade``), on the close-ups this test names.

Axes: the board is centred on the origin with its playing surface at z = 0; file a at -X, rank 1 (White) at -Y.
"""


import oneshot_kit
import parallel_kits as chess
import scenario_build_kit as kit

ID = "oneshot_chess"
TITLE = "A chess set in its starting position"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "chess-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
LIMITS = {"wall_s": 3600.0, "job_wait_s": 0.0, "cost_usd": 40.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (0.42, -0.62, 0.38), "target": (0.0, 0.0, 0.02), "lens": 50.0}
CLASSES = ("King", "Queen", "Bishop", "Knight", "Rook", "Pawn")
# The white piece of each class the close-ups look at, and the square in front of it they look from.
CLOSE_UP_SQUARES = {"King": "e1", "Queen": "d1", "Bishop": "c1", "Knight": "b1", "Rook": "a1", "Pawn": "e2"}


def _close_up(piece, colour="White"):
    square = CLOSE_UP_SQUARES[piece] if colour == "White" else CLOSE_UP_SQUARES[piece][0] + (
        "8" if CLOSE_UP_SQUARES[piece][1] == "1" else "7")
    x, y = chess.square_centre(square)
    toward = -1 if colour == "White" else 1
    height = chess.HEIGHTS[piece]
    if piece == "Knight":   # In profile, from the board's -X edge just above the rook beside it (its neighbours on
        # the rank would hide a lower side view).
        return {"location": (x - 0.15, y - 0.04, 0.2), "target": (x, y, height * 0.6), "lens": 70.0,
                "role": "closeup", "label": "Close-up: the %s knight on %s, in profile" % (colour.lower(), square)}
    return {"location": (x + 0.12, y + toward * 0.22, height * 1.4), "target": (x, y, height * 0.5), "lens": 60.0,
            "role": "closeup", "label": "Close-up: the %s %s on %s" % (colour.lower(), piece.lower(), square)}


CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "top": {"location": (0.0, 0.0, 1.5), "target": (0.0, 0.0, 0.0), "ortho_scale": 0.86, "role": "reference",
            "label": "The board from above (orthographic, White at the bottom)"},
    "front": {"location": (0.0, -2.0, 0.05), "target": (0.0, 0.0, 0.05), "ortho_scale": 0.5, "role": "reference",
              "label": "Front elevation (orthographic, looking +Y)"},
    "side": {"location": (-2.0, 0.0, 0.05), "target": (0.0, 0.0, 0.05), "ortho_scale": 0.5, "role": "reference",
             "label": "Side elevation (orthographic, looking +X)"},
    "back_rank": {"location": (0.0, -0.9, 0.07), "target": (0.0, -0.175, 0.045), "lens": 50.0, "role": "reference",
                  "label": "White's back rank, a1 to h1 (from White's side)"},
    **{"closeup_" + piece.lower(): _close_up(piece) for piece in CLASSES},
    **{"closeup_black_" + piece.lower(): _close_up(piece, "Black") for piece in CLASSES},
}
BOARD = {"prefix": "Chess Board", "word": True}
SUBJECT = ({"prefix": ""},)
ANYTHING_BUT_THE_BOARD = {"prefix": "", "except": [BOARD]}

PROMPT = (
    "A wooden chess set, standing on its board in the starting position. Build it. The four attached pictures "
    "show the set I have in mind: the hero shot, the board from above with White at the bottom, White's back "
    "rank from White's side, and a white knight in profile. Take the style of the pieces from them (classic Staunton-style turned pieces) and "
    "design the details yourself; where the pictures and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find the board by its name, so name it exactly as written here and give "
    "everything else a meaningful name of its own. Keep the file tidy: nothing from the default scene (its cube, "
    "light or camera) remains, no unused leftovers, and a material on everything that shows.\n\n"
    "The board. 'Chess Board' (other parts of it, such as a frame, have names starting with 'Chess Board'): "
    "8 x 8 squares of 5 cm, so the playing area is 40 cm across, centred on the origin, its playing surface "
    "flat at z = 0. Files a to h run along +X, ranks 1 to 8 along +Y: a1 is the corner at (-0.175, -0.175) and "
    "White sits on the -Y side. The squares alternate light (pale maple) and dark (walnut), with a1 dark and h1 "
    "light. A darker wooden frame about 2.5 cm wide around the playing area.\n\n"
    "The pieces. Sixteen white pieces in pale ivory and sixteen black pieces in glossy ebony, each standing on "
    "the centre of its square in the usual starting position: White's rooks on a1 and h1, knights on b1 and g1, "
    "bishops on c1 and f1, queen on d1, king on e1 and the pawns along rank 2; Black mirrored on ranks 8 and 7, "
    "queen on d8 and king on e8. Heights: king 9.5 cm to the top of its cross, queen 8.3 cm, bishop 7.1 cm, "
    "knight 6 cm, rook 5.4 cm, pawn 4.6 cm. Each piece has a round base a little narrower than its square "
    "(the king's about 3.8 cm across, the pawn's about 2.8 cm). The king carries a cross, the queen a crown "
    "with a small ball on top, the bishop a mitre with a small ball, the rook a crenellated top, and each knight "
    "is a horse's head facing the other side of the board. Every white piece of a kind looks the same, and "
    "every black piece is the same design as its white counterpart.\n\n"
    "Light and camera. Soft, warm light from above and to one side so the pieces throw soft shadows on the "
    "board, and a camera named 'Hero Camera' at (0.42, -0.62, 0.38) looking at (0, 0, 0.02) with a 50 mm "
    "lens, as the scene's camera. That is the hero shot in the first picture.\n\n"
    "Before you call it done, look at the set from the hero camera next to the pictures and fix what is wrong.")


def _down(square, target, low, high, label, expect="hit"):
    x, y = chess.square_centre(square)
    check = {"kind": "probe", "origin": [x, y, 0.5], "direction": [0, 0, -1], "target": target, "expect": expect,
             "distance": 0.51 if expect == "miss" else 1.0, "label": label}
    if expect == "hit":
        check.update(axis="z", range=[low, high])
    return check


# The top of a piece on its square's centre line, from the height the brief gives: the centre of a rook's top may
# be hollow and a knight's may fall on its mane or muzzle, so those two are allowed lower. 15 % above for all.
TOP_SHARE = {"King": 0.75, "Queen": 0.75, "Bishop": 0.75, "Pawn": 0.75, "Rook": 0.6, "Knight": 0.45}


def checks():
    position = chess.starting_position()
    occupied = [_down(square, ANYTHING_BUT_THE_BOARD, chess.HEIGHTS[piece] * TOP_SHARE[piece],
                      chess.HEIGHTS[piece] * 1.15, "a %s %s on %s, %.1f cm tall" % (
                          colour.lower(), piece.lower(), square, chess.HEIGHTS[piece] * 100))
                for square, (colour, piece) in sorted(position.items())]
    empty = [_down(f + r, ANYTHING_BUT_THE_BOARD, None, None, "%s%s is empty" % (f, r), expect="miss")
             for f in chess.FILES for r in "3456"]
    surface = [_down(square, BOARD, -0.006, 0.006, "the playing surface at z = 0 on %s" % square)
               for square in ("a3", "h3", "d4", "e5", "a6", "h6")]
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "object", "target": BOARD, "type": "MESH", "label": "Chess Board exists"},
        {"kind": "bbox", "target": BOARD, "size": [[0.39, 0.5], [0.39, 0.5], [0.004, 0.08]], "max": [None, None, 0.0],
         "tolerance": 0.006, "label": "a 40 cm board (with its frame up to 50 cm), its top at z = 0"},
        *surface, *occupied, *empty,
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.05, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [48.0, 52.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


# The vision grade's classes (bench_parallel_grade): each piece design on both colours, and the board with the set.
_WHAT = {"King": "a king with a cross on top, 9.5 cm tall", "Queen": "a queen with a crown and a small ball, 8.3 cm",
         "Bishop": "a bishop with a mitre and a small ball, 7.1 cm", "Knight": "a knight, a horse's head, 6 cm",
         "Rook": "a rook with a crenellated top, 5.4 cm", "Pawn": "a pawn, 4.6 cm"}
GRADE_CLASSES = {
    **{piece: {"views": ["closeup_" + piece.lower(), "closeup_black_" + piece.lower()],
               "what": "The %ss (white and black): %s, Staunton style, white in pale ivory and black in glossy "
                       "ebony, both colours the same design." % (piece.lower(), _WHAT[piece])} for piece in CLASSES},
    "Board": {"views": ["top", "hero"],
              "what": "The board: 8 x 8 squares of 5 cm alternating pale maple and walnut, a1 (bottom left from "
                      "White's side) dark and h1 light, in a darker wooden frame.",
              "layout": "Every piece on its starting square: White's rooks a1/h1, knights b1/g1, bishops c1/f1, "
                        "queen d1, king e1, pawns on rank 2; Black mirrored on ranks 8 and 7 with its queen on d8."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The chess set", "references": ["hero", "top", "back_rank", "closeup_knight"],
          "prompt": PROMPT,
          "checks": CHECKS}]


def build_turn(bpy, turn_id):
    """The stand-in (the brief is one turn): what a plain build that follows the brief leaves behind."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    chess.build_chess(bpy)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Key Light", 55.0, -140.0, (1.0, 0.9, 0.78), 3.0, angle_deg=8.0)
    scene.world = kit.colour_world(bpy, "Studio", (0.32, 0.30, 0.28), strength=0.6)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    scene.camera.data.clip_start = 0.01
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
