# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Held-out cases for live parallel-agent trials (``parallel_live_session.py``): a lead and two workers.

Held out on purpose: none of these was used while phase 1 was built (development used a chess piece, a room, a
vehicle, a character, a lamp, a kettle and a mug). Every case uses the same message template (``prompt``), so no
wording is tuned to a case; each case gives only what is built and the closed checks its parts must pass.

Checks are deliberately broad (this tests the parallel flow, not modelling quality): exactly two parts, each built
by its own worker with at least ``min_meshes`` mesh objects and a material, and each part's overall size within the
case's plausible range in metres. A vision review of the screenshots comes on top.

The job is the finished scene: the workers model the parts and the lead assembles them here (the maintainer,
2026-10-25: "The lead should be the one that figure out the assembling the scene"). A trial passes only when every
worker's part was merged into the lead's scene by the lead (``angelo_workers_merge``) and is still there, every merge
placed its parts as built (its own placement check, ``worker_placement``, e2e and later, ran and warned of nothing; a
build whose merge checks no placement fails, since it cannot tell), and the lead's own edits added no object (the scene
gained no more than its merges brought).

History, every model graded by the rule of its time: the first grade required an unchanged document and failed every
merge; the second (cc07f7b88a9) let a lead merge but also passed one that left finished parts outside its scene, and
the first message ("Do not build the parts in this scene yourself", a summary-only follow-up) could read as "keep the
parts out of this scene" (Opus 5.5's leads left them unmerged and passed on e2e 878147790ac). Both were the test's
faults, not the models'; the message now asks for the parts assembled into this scene, the same for every case.

The fourth grade (the maintainer, 2026-10-25, on Sol's sailboat_dock on e2e 06092674722): "a light or world setting
the lead adds is not a built part (only geometry counts), and the dock part's size cap allows the water the lead
briefs (cap by the brief, not 15 m)". The lead had added a light and briefed 16 x 16 m of water; the test was wrong,
not the model. So only geometry the lead adds counts as building (``GEOMETRY``, and an instancer of it; lights,
cameras, world settings and empties never), and a part's size cap is what the lead's own brief for it asks
(``brief_size``, read from the recorded ``angelo_workers_start`` call, never from free text elsewhere) with
``BRIEF_SLACK``, or the case's range when the brief states no size. Same rule for every case and model.

The size cap's slack (the maintainer, 2026-10-25): "Proof size cap: max(case cap, largest briefed dimension x 1.10);
lower bound stays the case's." Every earlier trial was re-graded under both rules of the fourth grade (pa-p1
REGRADE-4.md). This is the live proof's grader; the benchmark's checks stay as frozen in its preregistration.
"""

import re

TEMPLATE = ("Build {what} in this scene. Use parallel workers: split the job into exactly two unique parts and start "
            "one worker per part with angelo_workers_start, each with a complete brief. Let the workers model the "
            "parts, not you, and assemble their finished parts into this scene.")
FOLLOW_UP = ("The workers have finished. Make sure the scene is assembled, then tell me in two sentences what each "
             "worker built.")
# A worker whose turn has ended with a reply, as angelo_workers_status names it (session_rail's tile kinds): done (its
# own plan reported done), replied (ended otherwise), idle, or merged (its part is in the lead's scene).
FINISHED = frozenset({"done", "replied", "idle", "merged"})
MERGE = "angelo_workers_merge"
START = "angelo_workers_start"
# What the lead may not build itself: geometry, and an instancer of it ("INSTANCER", an empty instancing a collection).
GEOMETRY = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "VOLUME", "POINTCLOUD", "CURVES", "GREASEPENCIL",
                      "GPENCIL", "INSTANCER"})
BRIEF_SLACK = 0.10   # Over the largest size a brief asks (bevels, overhangs, rounding): the maintainer's x1.10.
_NUMBER = r"(\d+(?:\.\d+)?)"
_METRES = r"\s*m(?:et(?:er|re)s?)?\b"
_APPROX = r"(?:about|around|roughly|approx(?:imately|\.)?|~)?\s*"
# Explicit sizes only: "16x16m", "16 m x 16 m", "4.8 x 1.8 x 6 metres", "length approximately 4.8m", "2 m tall".
# Positions ("x=1.35..3.35", "to z5.8") are never sizes.
_SIZE_FORMS = (re.compile(_NUMBER + r"(?:\s*m)?\s*(?:x|\u00d7|by)\s*" + _NUMBER + r"(?:(?:\s*m)?\s*(?:x|\u00d7|by)\s*"
                          + _NUMBER + r")?" + _METRES, re.I),
               re.compile(r"\b(?:length|width|height|depth|diameter|size)\s*(?:of\s*)?[=:]?\s*" + _APPROX + _NUMBER
                          + _METRES, re.I),
               re.compile(_NUMBER + _METRES + r"\s+(?:long|wide|tall|high|deep|across|in\s+diameter)\b", re.I))

CASES = {
    "bench_planter": {"what": "a wooden garden bench with a matching planter box", "size": (0.3, 3.0),
                      "min_meshes": 2},
    "desk_chair": {"what": "a computer desk with a monitor on it, and an office chair", "size": (0.4, 2.5),
                   "min_meshes": 2},
    "sailboat_dock": {"what": "a small sailboat and the wooden dock it is moored at", "size": (1.0, 15.0),
                      "min_meshes": 2},
}


def prompt(case):
    return TEMPLATE.format(what=CASES[case]["what"])


def lead_edits(messages):
    """``{"added", "merges", "briefs"}`` from the lead chat's rows: objects its own edits reported adding (every tool
    but a merge), each merge that ran (its result), and each part's brief as its ``angelo_workers_start`` call sent it
    (pure)."""
    added, merges, briefs = [], [], {}
    for row in messages or ():
        if row.get("tool") == START and isinstance(row.get("arguments"), dict):
            for part in row["arguments"].get("parts") or ():
                if isinstance(part, dict) and isinstance(part.get("brief"), str):
                    briefs[str(part.get("name"))] = part["brief"]
        result = row.get("result") if row.get("role") == "activity" else None
        if not isinstance(result, dict):
            continue
        if row.get("tool") == MERGE:
            if result.get("success") is True:
                merges.append(result)
        else:
            added.extend(((result.get("changes") or {}).get("added")) or ())
    return {"added": added, "merges": merges, "briefs": briefs}


def brief_size(brief):
    """The largest dimension (metres) a brief states in an explicit size form, or None when it states none."""
    found = [float(number) for form in _SIZE_FORMS for match in form.finditer(brief or "")
             for number in match.groups() if number]
    return max(found) if found else None


def _geometry(row, types):
    """Whether an added object counts as built geometry: its recorded type, else the type its edit reported; with no
    type at all, it counts (as every addition did before)."""
    if not isinstance(row, dict):
        return True
    kind = (types or {}).get(row.get("name")) or row.get("type")
    return not kind or str(kind) in GEOMETRY


def grade(case, parts, document_objects_before, document_objects_after, lead=None, placement_checked=False,
          in_scene=(), types=None):
    """Closed checks on the measured parts (``parallel_live_session.measure``) and the lead's own edits (``lead_edits``):
    ``{check: passed}``. ``placement_checked``: this build's merge checks where it placed each part; ``in_scene``: the
    names of the collections in the lead's scene at the end; ``types``: each object's type at the end ("INSTANCER" for
    an empty instancing a collection), when recorded."""
    spec = CASES[case]
    low, high = spec["size"]
    lead = lead or {"added": [], "merges": []}
    entries = [entry for merge in lead["merges"] for entry in merge.get("merged") or ()]
    brought = sum(entry.get("objects", 0) * entry.get("placements", 1) for entry in entries)
    new = set(document_objects_after) - set(document_objects_before)
    # The lead's own non-geometry additions (a light, a camera, an empty) are scene setup, never a built part; any
    # other new object a merge did not bring counts (its type unknown: geometry, as before).
    setup = {row.get("name") for row in lead["added"] if isinstance(row, dict) and not _geometry(row, types)}
    setup |= {name for name in new if (types or {}).get(name) and types[name] not in GEOMETRY}
    checks = {"two_parts": len(parts) == 2,
              "lead_built_no_part": not any(_geometry(row, types) for row in lead["added"])
              and len(new - setup) <= brought}
    merged = {str(entry.get("worker")) for entry in entries}
    collections = set(in_scene)
    checks["every_part_assembled"] = bool(parts) and all(
        part.get("state") == "merged" and str(part.get("worker")) in merged for part in parts) and all(
        entry.get("collection") in collections for entry in entries)
    checks["merged_as_built"] = bool(entries) and placement_checked and not any(
        entry.get("placement_warnings") for entry in entries)
    for index, part in enumerate(parts, start=1):
        size = part.get("size") or 0.0
        checks["part%d_meshes" % index] = part.get("meshes", 0) >= spec["min_meshes"]
        checks["part%d_material" % index] = part.get("materials", 0) >= 1
        stated = brief_size((lead.get("briefs") or {}).get(str(part.get("name"))))
        cap = max(high, stated * (1 + BRIEF_SLACK)) if stated is not None else high   # What its brief asked.
        checks["part%d_size" % index] = low <= size <= cap
        # Its turn completed (break-it P1-19 M3: every ended turn has a reply now; a failed, stopped or unsent one
        # is no finished part).
        checks["part%d_worker_finished" % index] = part.get("state") in FINISHED and part.get("turn") == "completed"
    return checks
