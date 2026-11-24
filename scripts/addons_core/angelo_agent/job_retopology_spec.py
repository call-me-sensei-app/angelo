# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The RETOPOLOGY job's request, operator call and receipt. No Blender dependency (doc/angelo/retopology.rst).

The job runs Object > Retopology (``object.retopology``) on the target as Blender's own window-manager job. It always
writes a new object beside the target, so the person's mesh is never replaced, and reads the facts the operator
measured on the result (its ``_angelo_retopology`` property) into the job's receipt: what the agent verifies with.
"""

MIN_FACES = 16
# Refinement stops at 24 million working triangles (retopo_remesh.cc); two million quads stay well inside it.
MAX_FACES = 2_000_000
DEFAULT_FACES = 5000
DEFAULT_MINUTES = 30
# Mirror planes through the object's origin, in its own axes: the result is exactly symmetric across each.
SYMMETRY = ("NONE", "X", "Y", "Z", "XY", "XZ", "YZ", "XYZ")
PROPERTY = "_angelo_retopology"

SCHEMA = {
    "faces": {"type": "integer", "minimum": MIN_FACES, "maximum": MAX_FACES},
    "symmetry": {"type": "string", "enum": list(SYMMETRY)},
}
# Sent with every request, so only what the agent chooses by; retopology.rst has the rest.
DESCRIPTION = "RETOPOLOGY adds an all-quad copy of target with about faces faces (default %d). " % DEFAULT_FACES


def validate(args, request, refuse):
    """Check and normalize the job's own fields into ``request``; ``refuse(message)`` raises the job's refusal."""
    faces = args.get("faces", DEFAULT_FACES)
    if type(faces) is not int or not MIN_FACES <= faces <= MAX_FACES:
        refuse("faces is a whole number of faces from %d to %s" % (MIN_FACES, f"{MAX_FACES:,}"))
    symmetry = args.get("symmetry", "NONE")
    if not isinstance(symmetry, str) or symmetry not in SYMMETRY:
        refuse("symmetry is one of " + ", ".join(SYMMETRY))
    request["faces"], request["symmetry"] = faces, symmetry


def operator_arguments(request, tag):
    """The ``object.retopology`` properties for one job. Never in place; a background job; tagged with the job."""
    return {"target_faces": request["faces"], "use_mesh_symmetry": False,
            "symmetry": set() if request["symmetry"] == "NONE" else set(request["symmetry"]),
            "keep_original": True, "as_background_job": True, "tag": tag}


def find_result(candidates, tag):
    """``(name, facts)`` of the object this job made, from ``(name, facts)`` pairs, or None."""
    for name, facts in candidates:
        if isinstance(facts, dict) and facts.get("tag") == tag:
            return name, facts
    return None


def receipt(name, facts):
    """The facts as the agent reads them: the new object's name and what was measured on it."""
    return {
        "object": name,
        "faces": facts.get("faces"),
        "target_faces": facts.get("target_faces"),
        "quad_ratio": facts.get("quad_ratio"),
        "irregular_vertices": facts.get("irregular_vertices"),
        "poles": {"3": facts.get("poles_3"), "5": facts.get("poles_5"), "6+": facts.get("poles_6_plus")},
        "boundary_vertices": facts.get("boundary_vertices"),
        # Two-sided sampled distance between the result and the source, over the source's bounding-box diagonal.
        "deviation_of_size": {"max": facts.get("hausdorff_max_of_size"), "mean": facts.get("hausdorff_mean_of_size")},
        "parts": {"kept": (facts.get("shells") or 0) - (facts.get("shells_dropped") or 0),
                  "left_out": facts.get("shells_dropped") or 0},
        "seconds": round((facts.get("milliseconds") or 0.0) / 1000.0, 1),
    }
