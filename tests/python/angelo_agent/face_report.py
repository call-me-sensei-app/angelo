# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How far each side's head and hair were from the pictures, feature by feature (host only; no Blender).

    python3 face_report.py RUN_FOLDER... [--out FACE-REPORT.md] [--json face-report.json]
    python3 face_report.py --calibration face-calibration.json [--out CALIBRATION.md]

A face or hair one-shot run's own report says which checks passed; this one
says by how much every feature was off, for every side and trial, in
millimetres and in pixels of the reference picture: the outlines, each
profile landmark, the eyes, the chin and jaw line, the mouth, the relief per
region and the hair. It reads what the harness already saved for each side
(``oneshot-scorecard.json``, and the stock-Blender compliance pass on the
saved scene, ``final-checks.json`` in the side's capture folder), so both
sides are read from the same grader run on the same kind of file. A number a
trial did not record is left blank, never guessed.
"""

import argparse
import json
import math
import sys
from pathlib import Path

FACE_KINDS = ("face_outline", "face_landmarks", "face_eyes", "face_jaw", "face_mouth", "face_relief")
HAIR_KINDS = ("hair_strands", "hair_shape", "hairline", "hair_length")


def _number(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _fmt(value, digits=2):
    value = _number(value)
    return "" if value is None else ("%.*f" % (digits, value))


def side_rows(folder):
    """One row per side and trial of a run folder: the face and hair checks' details, by kind."""
    folder = Path(folder)
    card_path = folder / "oneshot-scorecard.json"
    if not card_path.is_file():
        return []
    card = json.loads(card_path.read_text())
    rows = []
    for side in card.get("sides", []):
        capture = Path(side.get("capture_folder") or "")
        if capture and not capture.is_absolute():
            capture = folder / capture
        checks_path = capture / "final-checks.json" if side.get("capture_folder") else None
        results = json.loads(checks_path.read_text())["results"] if checks_path and checks_path.is_file() else []
        found = {}
        for entry in results:
            if entry["kind"] in FACE_KINDS + HAIR_KINDS:
                key = entry["kind"] + (":" + entry["details"].get("view", "") if entry["kind"] == "face_outline"
                                       and isinstance(entry.get("details"), dict) else "")
                found[key] = {"passed": entry["passed"], "details": entry.get("details") or {}}
        rows.append({"test": card.get("test"), "side": side.get("label"), "verdict": side.get("verdict"),
                     "model": card.get("model"), "checks": found, "read": bool(results)})
    return rows


def summary(row):
    """The headline numbers of one side and trial."""
    checks = row["checks"]

    def detail(key, *path):
        value = checks.get(key, {}).get("details", {})
        for step in path:
            value = value.get(step, {}) if isinstance(value, dict) else {}
        return value if not isinstance(value, dict) else None

    landmarks = (checks.get("face_landmarks", {}).get("details", {}) or {}).get("landmarks", {})
    worst = max(landmarks.items(), key=lambda item: item[1].get("error_mm") or 0.0, default=(None, {}))
    eyes = (checks.get("face_eyes", {}).get("details", {}) or {}).get("eyes", {})
    mouth = (checks.get("face_mouth", {}).get("details", {}) or {}).get("points", {})
    relief = (checks.get("face_relief", {}).get("details", {}) or {}).get("regions", {})
    worst_region = max(relief.items(), key=lambda item: item[1].get("p90_mm") or 0.0, default=(None, {}))
    hair = (checks.get("hair_shape", {}).get("details", {}) or {}).get("views", {})
    return {
        "outline_front_p95_mm": detail("face_outline:front", "p95_mm"),
        "outline_side_p95_mm": detail("face_outline:side", "p95_mm"),
        "worst_landmark": worst[0], "worst_landmark_mm": worst[1].get("error_mm"),
        "eyes_max_mm": max((entry.get("error_mm") or 0.0 for entry in eyes.values()), default=None),
        "chin_mm": detail("face_jaw", "menton", "error_mm"), "jaw_line_mean_mm": detail("face_jaw", "line", "mean_mm"),
        "mouth_max_mm": max((entry.get("error_mm") or 0.0 for entry in mouth.values()), default=None),
        "worst_region": worst_region[0], "worst_region_p90_mm": worst_region[1].get("p90_mm"),
        "hair_iou": {view: entry.get("hair_iou") for view, entry in hair.items()},
        "hair_outline_p95_mm": max((entry.get("outline_p95_mm") or 0.0 for entry in hair.values()), default=None),
        "hairline_mean_mm": detail("hairline", "mean_mm"),
        "length_mm": {key: entry.get("error_mm") for key, entry in
                      (checks.get("hair_length", {}).get("details", {}) or {}).items() if isinstance(entry, dict)},
        "strands": detail("hair_strands", "strands"),
    }


def markdown(rows):
    lines = ["# Face and hair: how far each feature was from the pictures", "",
             "Millimetres in the reference pictures' own views (face pictures 0.4 mm a pixel, hair pictures "
             "0.55 mm a pixel). Read from the stock-Blender compliance pass on each side's saved scene. A blank is "
             "a number the trial did not record.", "",
             "| Test | Side | Verdict | Outline front / side p95 | Worst landmark | Eyes | Chin / jaw line | Mouth "
             "| Worst relief region (p90) |",
             "| --- |" + " --- |" * 8]
    hair_lines = []
    for row in rows:
        if not row["read"]:
            lines.append("| %s | %s | %s | not read | | | | | |" % (row["test"], row["side"], row["verdict"]))
            continue
        found = summary(row)
        if any(key.startswith("face_") for key in row["checks"]):
            lines.append("| %s | %s | %s | %s / %s | %s %s | %s | %s / %s | %s | %s %s |" % (
                row["test"], row["side"], row["verdict"], _fmt(found["outline_front_p95_mm"]),
                _fmt(found["outline_side_p95_mm"]), found["worst_landmark"] or "", _fmt(found["worst_landmark_mm"]),
                _fmt(found["eyes_max_mm"]), _fmt(found["chin_mm"]), _fmt(found["jaw_line_mean_mm"]),
                _fmt(found["mouth_max_mm"]), found["worst_region"] or "", _fmt(found["worst_region_p90_mm"])))
        if any(key.startswith("hair") for key in row["checks"]):
            hair_lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                row["test"], row["side"], row["verdict"], found["strands"] if found["strands"] is not None else "",
                " / ".join(_fmt(found["hair_iou"].get(view), 3) for view in ("front", "side", "back")),
                _fmt(found["hair_outline_p95_mm"]), _fmt(found["hairline_mean_mm"]),
                " / ".join(_fmt(found["length_mm"].get(key)) for key in ("back", "sides"))))
    if hair_lines:
        lines += ["", "| Test | Side | Verdict | Strands | Hair overlap front / side / back | Hair outline p95 "
                      "(worst view) | Hairline mean | Length back / sides |", "| --- |" + " --- |" * 7] + hair_lines
    per_landmark = {}
    for row in rows:
        for name, entry in ((row["checks"].get("face_landmarks", {}).get("details", {}) or {})
                            .get("landmarks", {}).items()):
            per_landmark.setdefault(name, []).append((row["test"], row["side"], entry.get("error_mm"),
                                                      entry.get("forward_mm"), entry.get("up_mm")))
    if per_landmark:
        lines += ["", "## Every profile landmark", "", "| Landmark | Test | Side | Error | Forward | Up |",
                  "| --- |" + " --- |" * 5]
        for name in sorted(per_landmark):
            for test, side, error, forward, up in per_landmark[name]:
                lines.append("| %s | %s | %s | %s | %s | %s |" % (name, test, side, _fmt(error), _fmt(forward),
                                                                  _fmt(up)))
    return "\n".join(lines) + "\n"


def _feature_errors(readings):
    """``{feature: error_mm}`` of one graded build (``face_variants --calibrate``'s readings, by check label)."""
    found = {}
    for label, details in (readings or {}).items():
        if not isinstance(details, dict):
            continue
        if "landmarks" in details:
            for name, entry in details["landmarks"].items():
                found["landmark " + name] = entry.get("error_mm")
        if "eyes" in details:
            for name, entry in details["eyes"].items():
                found["eye " + name] = entry.get("error_mm")
        if "menton" in details:
            found["chin bottom"] = details["menton"].get("error_mm")
            found["jaw line mean"] = details["line"].get("mean_mm")
        if "points" in details:
            for name, entry in details["points"].items():
                found["mouth " + name] = entry.get("error_mm")
        if details.get("view") and "p95_mm" in details:
            found["outline %s p95" % details["view"]] = details["p95_mm"]
        if "regions" in details:
            for name, entry in details["regions"].items():
                found["relief %s p90" % name] = entry.get("p90_mm")
        if "views" in details:
            for name, entry in details["views"].items():
                found["hair %s outline p95" % name] = entry.get("outline_p95_mm")
                found["hair %s overlap (IoU)" % name] = entry.get("hair_iou")
        if "columns" in details and "mean_mm" in details and "menton" not in details:
            found["hairline mean"] = details["mean_mm"]
        for key in ("back", "sides"):
            if isinstance(details.get(key), dict) and "error_mm" in details[key]:
                found["length " + key] = details[key]["error_mm"]
    return found


def _as_details(analytic):
    """``face_truth.errors`` keyed like the checks' details, for ``_feature_errors``."""
    return {"landmarks": {"landmarks": analytic["landmarks"]}, "eyes": {"eyes": analytic["eyes"]},
            "jaw": analytic["jaw"], "mouth": {"points": analytic["mouth"] or {}},
            "outline front": dict(analytic["outline front"], view="front"),
            "outline side": dict(analytic["outline side"], view="side"), "relief": {"regions": analytic["relief"]}}


def calibration(table):
    """The graders' noise floor (the worst reading over every build that follows the brief) and, beside each
    known defect, what it read; markdown."""
    lines = ["# Face and hair graders: noise floor and calibration", "",
             "Noise floor: the worst error each feature read over every other way of building the same ground "
             "truth (meshed coarser and finer, triangulated, voxel-remeshed, subdivided). IoU is the lowest "
             "overlap. A defect lists the checks it failed and the features that read more than 1 mm.", ""]
    tests = []
    for row in table:
        if row["test"] not in tests:
            tests.append(row["test"])
    for test in tests:
        rows = [row for row in table if row["test"] == test]
        floor = {}
        for row in rows:
            if not row["follows_the_brief"] or "particle" in row["variant"]:
                continue
            for feature, value in _feature_errors(row["readings"]).items():
                if not isinstance(value, (int, float)):
                    continue
                if feature.endswith("(IoU)"):
                    floor[feature] = min(floor.get(feature, 1.0), value)
                else:
                    floor[feature] = max(floor.get(feature, 0.0), value)
        others = sum(1 for row in rows if row["follows_the_brief"] and "particle" not in row["variant"])
        lines += ["## %s" % test, "", "%d builds that follow the brief, %d as expected; %d defects, %d as expected." % (
            others, sum(row["as_expected"] for row in rows if row["follows_the_brief"]),
            sum(not row["follows_the_brief"] for row in rows),
            sum(row["as_expected"] for row in rows if not row["follows_the_brief"])), "",
                  "| Feature | Noise floor |", "| --- | --- |"]
        lines += ["| %s | %s |" % (feature, _fmt(value, 3)) for feature, value in sorted(floor.items())]
        lines += ["", "| Defect | As expected | Failed checks | Features reading over 1 mm |", "| --- | --- | --- | --- |"]
        for row in rows:
            if row["follows_the_brief"] and "particle" not in row["variant"]:
                continue
            if "particle" in row["variant"]:
                reading = row["readings"]
                lines.append("| %s | %s | | %s strands of %s asked; roots within %s mm of the head; median length "
                             "%s mm |" % (row["variant"], row["as_expected"], reading.get("strands"),
                                          reading.get("asked"), reading.get("root_distance_max_mm"),
                                          reading.get("length_median_mm")))
                continue
            measured = _feature_errors(row["readings"])
            analytic = _feature_errors(_as_details(row.get("analytic"))) if row.get("analytic") else {}
            big = sorted((feature, value) for feature, value in measured.items()
                         if isinstance(value, (int, float)) and not feature.endswith("(IoU)") and value > 1.0)
            lines.append("| %s | %s | %s | %s |" % (row["variant"], row["as_expected"], "; ".join(row["failed"]),
                                                     "; ".join("%s %s%s" % (f, _fmt(v), (" (design %s)" % _fmt(
                                                         analytic[f]) if isinstance(analytic.get(f), (int, float))
                                                         else "")) for f, v in big[:8])))
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folders", nargs="*")
    parser.add_argument("--out")
    parser.add_argument("--json")
    parser.add_argument("--calibration", help="a face_variants --calibrate table instead of run folders")
    args = parser.parse_args(argv)
    if args.calibration:
        text = calibration(json.loads(Path(args.calibration).read_text()))
        if args.out:
            Path(args.out).write_text(text)
        sys.stdout.write(text)
        return 0
    rows = [row for folder in args.folders for row in side_rows(folder)]
    text = markdown(rows)
    if args.out:
        Path(args.out).write_text(text)
    if args.json:
        Path(args.json).write_text(json.dumps([dict(row, summary=summary(row)) for row in rows], indent=1,
                                              default=str))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
