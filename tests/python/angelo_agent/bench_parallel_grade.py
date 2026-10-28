# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The vision grade of ``bench_parallel_vs_single.py`` runs, per part class, blind to the arm (BILLED: model calls).

    python3 bench_parallel_grade.py --authorize-billed-runs --run DIR --claude-config-dir PROFILE [--seed N]

For every captured trial of both arms (``DIR/single``, ``DIR/parallel``) and every grade class of the test
(``GRADE_CLASSES`` of its scenario module: a part class and the cameras that show it), one grader call sees the
reference pictures of those cameras (rendered from the stand-in) and the trial's own renders from the same cameras
(the scorecard's end-of-session capture, one pipeline for every scene). The images are copied under neutral names
(``reference-1.png``, ``built-1.png``) and the calls run in an order shuffled by ``--seed``, so neither the file
names, the folder nor the order tells the grader which arm built what. Each call returns 0-4 on every criterion of
the rubric (``CRITERIA``, plus ``layout`` for a class that names one); a class's score is their mean. One call per
item, the same grader (Claude Code with ``--model``, Opus 5.5 by default) and the same prompt for both arms.
Writes ``DIR/grades.json``; ``bench_parallel_report.py`` summarizes it.
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenario_specs as specs  # noqa: E402

CRITERIA = {
    "recognisable": "How clearly the built part reads as what the brief asks for (0: not at all, 4: unmistakably).",
    "proportions": "How well its shape and proportions match the reference pictures and the brief's sizes.",
    "materials": "How well its materials and colours match the brief (finish, colour, roughness as described).",
}
LAYOUT = "How correctly it is laid out as the brief states (positions, counts, pattern, which item stands where)."
PROMPT = (
    "You grade one part of a 3D scene that an AI built from a written brief. The reference pictures show what was "
    "asked for; the built pictures show the AI's scene from the same cameras. Grade only the part named below, "
    "comparing the built pictures with the references and the brief.\n\nPart: {part}\nWhat it is: {what}\n\n"
    "Reference pictures: {references}\nBuilt pictures: {built}\n\nRead every picture, then score each criterion "
    "from 0 to 4 (whole numbers):\n{criteria}\n\nAnswer with one JSON object only, no other text: "
    '{{{keys}, "notes": "<one sentence>"}}')


def capture_of(trial_folder):
    """A trial's scorecard capture folder, or None: a trial that crashed after its message has none, and the report
    scores its every class 0 (BENCH-PREREG Amendment C)."""
    capture = next(Path(trial_folder).rglob("scenario-capture"), None)
    return capture if capture is not None and capture.is_dir() else None


def trial_folders(arm_dir):
    """``[(trial, capture folder)]`` of an arm's captured trials."""
    found = []
    for folder in sorted(Path(arm_dir).glob("angelo-trial-[0-9][0-9]")):
        capture = capture_of(folder)
        if capture is not None:
            found.append((int(folder.name.rsplit("-", 1)[1]), capture))
    return found


def items(run, test):
    module = specs.module(test)
    references = specs.references_dir(test)
    for arm in ("single", "parallel"):
        for trial, capture in trial_folders(Path(run) / arm):
            for part, spec in module.GRADE_CLASSES.items():
                yield {"arm": arm, "trial": trial, "part": part, "what": spec["what"], "layout": spec.get("layout"),
                       "references": [references / (view + ".jpg") for view in spec["views"]],
                       "built": [capture / ("render-%s.png" % view) for view in spec["views"]]}


PNG_DROPPED = {b"tEXt", b"zTXt", b"iTXt", b"tIME", b"eXIf"}   # Text and time: a render's path names its arm.


def neutral_copy(source, target):
    """``source``'s pixels at ``target`` without the metadata that can name a trial's folder, and so its arm (a PNG's
    text and time chunks, a JPEG's APP1-APP15 and comment segments; break-it E2E-3 L-15). Raises on a file it
    cannot read, never copies it as it is."""
    data = Path(source).read_bytes()
    out = bytearray()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        out += data[:8]
        at = 8
        while at < len(data):
            length = int.from_bytes(data[at:at + 4], "big")
            kind, end = data[at + 4:at + 8], at + 12 + length
            if end > len(data):
                raise ValueError("a truncated PNG: %s" % source)
            if kind not in PNG_DROPPED:
                out += data[at:end]
            at = end
    elif data[:2] == b"\xff\xd8":
        out += data[:2]
        at = 2
        while at < len(data):
            if data[at] != 0xFF:
                raise ValueError("an unreadable JPEG: %s" % source)
            marker = data[at + 1]
            if marker == 0xDA:   # Start of scan: the image data to the end, as it is.
                out += data[at:]
                break
            length = int.from_bytes(data[at + 2:at + 4], "big")
            if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
                out += data[at:at + 2 + length]
            at += 2 + length
    else:
        raise ValueError("neither PNG nor JPEG: %s" % source)
    Path(target).write_bytes(bytes(out))


def grade_prompt(item, folder):
    names = {"references": [], "built": []}
    for kind in names:
        for index, source in enumerate(item[kind], start=1):
            target = folder / ("%s-%d%s" % ("reference" if kind == "references" else "built", index, source.suffix))
            neutral_copy(source, target)
            names[kind].append(str(target))
    criteria = dict(CRITERIA, **({"layout": LAYOUT} if item["layout"] else {}))
    return PROMPT.format(part=item["part"], what=item["what"] + (" " + item["layout"] if item["layout"] else ""),
                         references=", ".join(names["references"]), built=", ".join(names["built"]),
                         criteria="\n".join("- %s: %s" % pair for pair in criteria.items()),
                         keys=", ".join('"%s": <0-4>' % key for key in criteria)), list(criteria)


def parse_answer(text, keys):
    """The grader's scores, or None when its answer is not exactly the asked JSON."""
    match = re.search(r"\{.*\}", text or "", re.S)
    try:
        value = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    if not isinstance(value, dict) or not all(isinstance(value.get(k), int) and 0 <= value[k] <= 4 for k in keys):
        return None
    return {**{k: value[k] for k in keys}, "notes": str(value.get("notes", ""))[:400]}


def ask(claude, model, config_dir, prompt, folder):
    environment = {"CLAUDE_CONFIG_DIR": str(config_dir), "PATH": "/usr/bin:/bin:/usr/local/bin",
                   "HOME": str(Path.home())}
    # Preserve only the bounded non-secret QA owner, never inherited routing or
    # credentials, so the slot also owns a grader orphan after its parent exits.
    owner = os.environ.get("ANGELO_QA_PROCESS_OWNER", "")
    if len(owner) == 32 and all(char in "0123456789abcdef" for char in owner):
        environment["ANGELO_QA_PROCESS_OWNER"] = owner
    try:
        result = subprocess.run([claude, "-p", prompt, "--model", model, "--output-format", "json",
                                 "--allowedTools", "Read", "--add-dir", str(folder)], cwd=folder, env=environment,
                                capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:   # That item has no score, reported; the other items keep theirs (E2E-3 L-18).
        return ""
    try:
        return json.loads(result.stdout).get("result", "")
    except ValueError:
        return ""


def save(run, value):
    """``grades.json``, replaced whole (a reader never sees half a file)."""
    temporary = run / "grades.json.tmp"
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(run / "grades.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--authorize-billed-runs", action="store_true", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--claude-config-dir", required=True)
    parser.add_argument("--claude", default=str(Path.home() / ".local/bin/claude"))
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--seed", type=int, default=20261024)
    args = parser.parse_args(argv)
    run = Path(args.run)
    test = specs.scenario_id(json.loads((run / "interleave.json").read_text())["task_set"])
    queue = list(items(run, test))
    random.Random(args.seed).shuffle(queue)
    grades = []
    for item in queue:
        error, answer, keys = None, None, []
        with tempfile.TemporaryDirectory() as tmp:
            try:
                prompt, keys = grade_prompt(item, Path(tmp))
                answer = parse_answer(ask(args.claude, args.model, args.claude_config_dir, prompt, Path(tmp)), keys)
            except Exception as failure:   # noqa: BLE001 - one item's failure leaves it unscored, reported; the
                error = "%s: %s" % (type(failure).__name__, str(failure)[:300])   # others keep their grades.
        grades.append({"arm": item["arm"], "trial": item["trial"], "part": item["part"], "scores": answer,
                       "score": round(sum(answer[k] for k in keys) / len(keys), 3) if answer else None,
                       **({"error": error} if error else {})})
        print("GRADE", json.dumps(grades[-1]), flush=True)
        save(run, {"test": test, "grader": args.model, "seed": args.seed, "criteria": CRITERIA, "layout": LAYOUT,
                   "grades": grades})   # After every item: a stop mid-run keeps every billed grade so far.
    return 0 if all(g["scores"] for g in grades) else 1


if __name__ == "__main__":
    sys.exit(main())
