# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The verdict and report of a one-shot run of ``bench_mcp_vs_angelo.py`` (host side; sends nothing).

``finish`` runs after the trials (and for ``--summarize-only``):

#. each side's saved scene is captured in stock Blender by ``oneshot_job.py``
   (the scenario scorecard then reuses those captures and re-runs every
   check on the saved scene);
#. the final still is rendered from the brief's camera position with the
   scene's own settings, and it and the agent's own still are measured
   (``oneshot_job.py --job final``);
#. the verdict: **passed** only when the agent ended its one turn by itself,
   every closed check passed (the still it had to save included) and every
   pixel check passed. A stopped turn is graded and reported, never passed.
   **inconclusive** when the only thing short of that is checks the grader
   could not decide: never a pass, and shown apart from a measured fail in
   every table, with the closed checks as passed / failed / inconclusive.
   The still judged is the file the agent names as final
   (``final_render``), and the report says which and why
   (``final_still_judged``). A test that asks for no still (a mini) has
   no pixel check; its still is rendered for people only. No model-judged score exists in the totals;
   ``opinion`` is reserved for one and is always labelled as such;
#. the path metrics of each side (``oneshot_metrics``).

It writes ``oneshot-scorecard.json``, ``ONESHOT-REPORT.md`` and
``oneshot-report.html`` (references, each side's stills and fixed views side
by side) next to the bench's ``SUMMARY.md``.
"""

import hashlib
import html
import json
import shutil
from pathlib import Path

import bench_integrity
import final_render
import oneshot_checks
import oneshot_metrics as metrics
import oneshot_produced
import oneshot_references
import oneshot_tables as tables
import scenario_references as references
import scenario_report
import scenario_scorecard
import scenario_specs as specs

HERE = Path(__file__).resolve().parent
SCORECARD, REPORT, PAGE = "oneshot-scorecard.json", "ONESHOT-REPORT.md", "oneshot-report.html"
OWN_CAMERA, BRIEF_CAMERA = "default scene", "default scene from the brief's camera"


def trial_folders(output):
    """``[(side, number, trial folder, saved scene)]`` of a run folder, MCP trials first."""
    found = []
    for folder in sorted(Path(output).glob("mcp-trial-*")):
        if folder.is_dir() and (folder / "trial.json").is_file():
            found.append(("mcp", int(folder.name.rsplit("-", 1)[1]), folder, folder / "final.blend"))
    for folder in sorted(Path(output).glob("angelo-trial-*")):
        if folder.is_dir() and (folder / "eval-live-agent.json").is_file():
            blend = folder / "trial-01" / "qa" / "final.blend"
            found.append(("angelo", int(folder.name.rsplit("-", 1)[1]), folder, blend))
    return found


def _job(blender, name, job, folder, extra, log):
    code = references.run_job(blender, ["--job", job, "--scenario", name, "--out", str(folder), *extra],
                              folder / ".scratch", folder / log, script=HERE / "oneshot_job.py")
    shutil.rmtree(folder / ".scratch", ignore_errors=True)
    return code == 0


def prepare(output, name, blender=references.STOCK_BLENDER, samples=48):
    """Capture every saved scene with the one-shot job, where ``scenario_scorecard.capture`` looks for it."""
    for _, _, _, blend in trial_folders(output):
        folder = blend.parent / "scenario-capture"
        if blend.is_file() and not ((folder / "capture.npz").is_file() and (folder / "final-checks.json").is_file()):
            folder.mkdir(parents=True, exist_ok=True)
            _job(blender, name, "capture", folder, ["--blend", str(blend), "--samples", str(samples)], "capture.log")


def raw_record(side, folder):
    """``(task record or None, one-shot state, trial data)`` of one trial, as its driver wrote them."""
    if side == "mcp":
        data = json.loads((folder / "trial.json").read_text())
        return (data.get("tasks") or [None])[0], data.get("oneshot") or {}, data
    trial = (json.loads((folder / "eval-live-agent.json").read_text()).get("trials") or [{}])[0]
    record = (trial.get("tasks") or [None])[0]
    return record, (record or {}).get("oneshot") or {}, trial


def stream_lines(folder, runtime):
    path = folder / ("codex-stream.jsonl" if runtime == "codex" else "claude-stream.jsonl")
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def final_still(name, blend, still, blender):
    """``oneshot_job.py --job final`` on a saved scene (reused when already made); its measurements or an error."""
    folder = blend.parent / "oneshot-final"
    if not (folder / "final.json").is_file():
        folder.mkdir(parents=True, exist_ok=True)
        extra = ["--blend", str(blend)] + (["--still", str(still)] if still else [])
        if not _job(blender, name, "final", folder, extra, "final.log"):
            return {"error": "the final-still job failed; see %s" % (folder / "final.log")}, folder
    return json.loads((folder / "final.json").read_text()), folder


def pixels(final):
    """Pixel checks of the harness's still and of the agent's own, against the factory scene's renders."""
    baselines = {name: facts.get("thumbnail") for name, facts in (final.get("baselines") or {}).items()}
    checks = oneshot_checks.pixel_checks(final.get("harness") or {"error": final.get("error", "not rendered")},
                                         {BRIEF_CAMERA: baselines.get(BRIEF_CAMERA)}, "harness still")
    if "agent" in final:
        checks += oneshot_checks.pixel_checks(final["agent"], baselines, "agent's own still")
    return checks


def verdict_of(reasons):
    """``passed`` with no reason; ``inconclusive`` when every reason is a check the grader could not decide
    (never a pass, and not a measured fail); otherwise ``failed``."""
    if not reasons:
        return "passed"
    return "inconclusive" if all(reason.startswith("check inconclusive: ") for reason in reasons) else "failed"


def side_result(name, side, number, folder, blend, card_trial, model, blender):
    """One side's verdict, check results, pixel checks and path metrics."""
    scenario = specs.module(name)
    record, state, data = raw_record(side, folder)
    runtime = data.get("runtime") or (record or {}).get("runtime") or ("claude" if side == "mcp" else None)
    title = scenario_report.side_label({"side": side, "runtime": runtime})
    result = {"side": side, "trial": number, "runtime": runtime, "folder": str(folder), "side_label": title,
              "label": "%s %d" % (title, number),
              # What the trial recorded it was sent (bench_integrity), not what the module says today.
              "prompt_sha256": (record or {}).get("prompt_sha256")}
    if record is None:
        return dict(result, verdict="failed", reasons=["no message was sent: %s" % (
            data.get("aborted") or data.get("error") or "the trial did not start")], checks=None, pixels=[], path=None)
    host_check = record.get("host_check") or {}
    failed = scenario_scorecard._failed_checks(host_check)
    still = state.get("still") if isinstance(state.get("still"), str) and Path(state["still"]).is_file() else None
    final, final_folder = (final_still(name, blend, still, blender) if blend.is_file()
                           else ({"error": "no saved scene"}, None))
    asked = specs.final_still(name)
    pixel_checks = pixels(final) if asked else []   # Only a brief that asks for a still.
    if side == "mcp":
        path = metrics.mcp_path(record, stream_lines(folder, runtime), state, model, (asked or {}).get("name", ""))
    else:
        path = metrics.angelo_path(record, model)
    reasons = ["stopped: %s" % path["stop"]["reason"]] if path["stop"]["reason"] != "finished" else []
    reasons += ["check %s: %s" % ("inconclusive" if entry.get("inconclusive") else "failed", entry["label"])
                for entry in failed]
    reasons += ["%s" % entry["label"] for entry in pixel_checks if not entry["passed"]]
    if path["messages_typed"] != 1:
        reasons.append("%s messages were typed, not one" % metrics.unknown(path["messages_typed"]))
    if not host_check.get("total"):
        reasons.append("no check ran")
    # What the side did write and start, whatever the names: a missing still is then "rendered something else"
    # or "rendered nothing". Only the Angelo side has render jobs; a run of an older harness recorded no files.
    produced, jobs = state.get("produced"), (record.get("jobs") or []) if side == "angelo" else None
    late = produced is None
    if late:   # A run of an older harness: what is still in its trial folder (its temporary folder is gone).
        produced = oneshot_produced.images([folder], None)
    unsaved = asked and any(entry.get("kind") == "output" and not entry.get("passed")
                            for entry in host_check.get("results") or ())
    judged = next((((entry.get("details") or {}).get("final")) for entry in host_check.get("results") or ()
                   if entry.get("kind") == "output"), None) if asked else None
    verdict = verdict_of(reasons)
    return dict(result, verdict=verdict, grade=verdict,
                reasons=reasons, status=record.get("status"), produced=produced, jobs=jobs, produced_late=late,
                still_missing=oneshot_produced.missing_still(asked["name"], produced, jobs, late) if unsaved else None,
                # Which file was judged as the final still and why (final_render); None before the rule existed.
                final_still_judged=judged,
                checks={"passed": host_check.get("passed", 0), "total": host_check.get("total", 0), "failed": failed},
                saved_scene_checks=(card_trial or {}).get("end_of_session_checks"),
                pixels=pixel_checks, final=final, final_folder=str(final_folder) if final_folder else None,
                agent_still=still, path=path, hygiene=(card_trial or {}).get("hygiene"),
                allowances=state.get("allowances") if side == "mcp" else None,
                # View scores compare with the ground truth: meaningful only when it is the reference's source.
                scores=(card_trial or {}).get("scores") if scenario.GROUND_TRUTH == "reference" else None,
                capture_folder=(card_trial or {}).get("capture_folder"), opinion=None)


def collect(output, task_set, card, meta=None, blender=references.STOCK_BLENDER):
    """The one-shot scorecard of a run folder; written to ``oneshot-scorecard.json``."""
    name = specs.scenario_id(task_set)
    scenario, meta = specs.module(name), meta or {}
    model = (meta.get("plan") or {}).get("model") or ""
    by_key = {(trial["side"], trial["trial"]): trial for trial in (card or {}).get("trials") or ()}
    sides = [side_result(name, side, number, folder, blend, by_key.get((side, number)), model, blender)
             for side, number, folder, blend in trial_folders(output)]
    prompt = scenario.TURNS[0]["prompt"]
    result = {"test": task_set, "title": scenario.TITLE, "finished_at": meta.get("finished_at"),
              "dry_run": bool(meta.get("dry_run_scenario")), "model": model or None,
              "runtime": (meta.get("plan") or {}).get("runtime"), "effort": (meta.get("plan") or {}).get("effort"),
              "limits": dict(scenario.LIMITS), "final_still": specs.final_still(name),
              "ground_truth": getattr(scenario, "GROUND_TRUTH", "reference"),
              "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "references": oneshot_references.attached(name), "reference_digest": specs.digest(name),
              "sides": sides, "unknown": tables.unknown_in(sides), "conditions": meta.get("conditions"),
              "cost_basis": tables.cost_basis([side["path"] for side in sides]),
              "grading": "closed checks and pixel statistics only; no model-judged score is part of any total"}
    (Path(output) / SCORECARD).write_text(json.dumps(result, indent=2, default=str))
    return result


def finish(output, task_set, results=None, meta=None, blender=references.STOCK_BLENDER):
    """Captures, final stills, both scorecards and the report of a one-shot run folder."""
    name = specs.scenario_id(task_set)
    prepare(output, name, blender)
    card = scenario_scorecard.collect(output, task_set, results, blender)
    result = collect(output, task_set, card, meta, blender)
    write(output, result)
    return result


# ---------------------------------------------------------------- report files

def _source_text(source):
    if not source:
        return "rendered from the scenario's ground truth"
    if source.get("kind") == "crop":
        box = "the whole picture" if not source.get("box") else "pixels %s" % (source["box"],)
        return "cut from %s (SHA-256 %s): %s, enlarged %s times" % (source["of"], source["source_sha256"], box,
                                                                     source.get("scale", 1))
    return "rendered from the ground truth in %s, camera %s (%s)" % (source["ground_truth"], source["camera"],
                                                                      source.get("blender"))


def markdown(result):
    sides = result["sides"]
    limits = result["limits"]
    who = "scripted models (unbilled dry run)" if result["dry_run"] else "model %s, runtime %s, effort %s" % (
        result["model"], result["runtime"], result["effort"])
    images = len(result["references"] or ())
    job = ", %g s more for a render job it started" % limits["job_wait_s"] if limits["job_wait_s"] else ""
    lines = ["# One-shot test: %s" % result["title"], "",
             "Test `%s`: one message per side with %s, then nothing; %s; finished %s. Limits: %g s for the turn%s, "
             "%g USD at list prices."
             % (result["test"], "%d reference image(s)" % images if images else "no reference image", who,
                result["finished_at"], limits["wall_s"], job, limits["cost_usd"]), "",
             *bench_integrity.markdown_lines(result.get("conditions")),   # Scripts and planned n.
             "## Result", "",
             "| Side | Verdict | Closed checks passed / failed / inconclusive | Pixel checks | Stop reason |",
             "| --- | --- | --- | --- | --- |"]
    for side in sides:
        path = side["path"]
        pixel = "%d / %d" % (sum(entry["passed"] for entry in side["pixels"]), len(side["pixels"])) \
            if side["pixels"] else "none asked"
        lines.append("| %s | %s | %s | %s | %s |" % (
            side["label"], side["verdict"].upper(), tables.checks_text(side["checks"]), pixel,
            path["stop"]["reason"] if path else "not started"))
    lines += ["", "Verdicts: %s." % tables.verdicts_line(sides),
              "", "A side passes only when it ended its one turn by itself and every closed check and pixel check "
              "passed. It is inconclusive when the only checks it did not pass are ones the grader could not decide: "
              "never a pass, and not a measured fail. No score judged by a model is counted."]
    if not result.get("final_still"):
        lines += ["", "This test asks for no still, so it has no pixel check; it starts from the factory scene and "
                  "does not ask for it to be cleared."]
    else:
        lines += ["", "The still judged is the file the agent names as final: its reply, then the last render's job "
                  "receipt, then the requested name (the same rule on every side)."]
        lines += ["- %s: %s" % (side["label"], final_render.describe(side.get("final_still_judged"))
                                if side.get("final_still_judged") else "the requested name (recorded before this rule)")
                  for side in sides if side["path"]]
    lines += ["", "## How each side got there", ""]
    columns = [(side["label"], side["path"], {
        "verdict": side["verdict"], "checks": tables.checks_text(side["checks"]),
        "produced": oneshot_produced.text(side.get("produced"), side.get("jobs"), side.get("produced_late"))
        if result.get("final_still") else None}) for side in sides if side["path"]]
    basis = result.get("cost_basis") or tables.cost_basis([side["path"] for side in sides])
    lines += tables.table(columns, basis) if columns else ["No side sent its message."]
    lines += ["", "Cost basis, the same for every side and trial: %s." % basis["label"]]
    for side in sides:
        if side["side"] == "angelo" and side["path"]:
            lines += ["", "%s. %s" % (side["label"], tables.intended_path(
                side["path"], side["checks"]["passed"], side["checks"]["total"],
                sum(bool(entry.get("inconclusive")) for entry in side["checks"].get("failed") or ())))]
    if len(sides) > len({tables.side_name(side) for side in sides}):
        lines += ["", "## Over the trials", "", tables.TRIALS_NOTE, ""] + tables.trials_header() \
            + tables.trials_rows(sides, basis=basis)
    lines += ["", "## Why a side did not pass", ""]
    for side in sides:
        lines.append("- **%s**: %s" % (side["label"], "passed." if side["verdict"] == "passed" else "%s: %s" % (
            side["verdict"], "; ".join(side["reasons"][:40]))))
        if side.get("still_missing"):
            lines.append("  - " + side["still_missing"])
        declined = ((side["path"] or {}).get("approvals") or {}).get("declined_tools")
        if declined:
            lines.append("  - The harness's approval policy declined: %s. That is the harness's rule (%s), not a "
                         "fault of the product." % (", ".join(declined), metrics.DECLINE_POLICY))
        for entry in ((side["checks"] or {}).get("failed") or [])[:30]:
            lines.append("  - %s%s: %s" % (entry.get("label"), " (inconclusive)" if entry.get("inconclusive") else "",
                                           json.dumps(entry.get("details"), default=str)[:240]))
        for entry in side["pixels"]:
            if not entry["passed"]:
                lines.append("  - %s: %s" % (entry["label"], json.dumps(entry.get("details"), default=str)[:240]))
    other = ["%s (%s)" % (side["label"], side.get("prompt_sha256") or "not recorded") for side in sides
             if side.get("prompt_sha256") != result["prompt_sha256"]]
    lines += ["", "## The brief", "", "Sent once to each side, unchanged (SHA-256 %s)%s:" % (
        result["prompt_sha256"], "" if not other else "; **these trials recorded another or no SHA-256: %s**"
        % ", ".join(other)), ""]
    lines += ["> " + line for line in result["prompt"].split("\n")]
    lines += ["", "## References", ""] + ([] if result["references"] else ["None: the message is text only."])
    for entry in result["references"] or ():
        lines.append("- `%s` (%d bytes, SHA-256 %s): %s" % (entry["file"], entry["bytes"], entry["sha256"],
                                                            _source_text(entry.get("source"))))
    allowed = next((side["allowances"] for side in sides if side.get("allowances")), None)
    if allowed:
        lines += ["", "## What the other side's client was allowed", ""] + ["- " + item for item in allowed]
    lines += ["", "## Not recorded", ""]
    if result["unknown"]:
        lines += ["Shown as \"unknown\" above, and what records each:", ""]
        lines += ["- `%s`: %s" % item for item in sorted(result["unknown"].items())]
    else:
        lines += ["Nothing this report lists as recordable was left unknown in this run."]
    lines += ["", "## Opinion (never counted)", "", "No model-judged comparison was run. People judge the pictures "
              "in `%s`." % PAGE, ""]
    return "\n".join(lines) + "\n"


def _copy(source, assets, name):
    return scenario_report._copy(source, assets, name)


def gather_images(output, result):
    """Relative paths: the attached references, and per side its stills and fixed-view renders."""
    assets = Path(output) / "report-assets"
    name = specs.scenario_id(result["test"])
    folder = specs.references_dir(name)
    images = {"references": [(entry["view"], _copy(folder / entry["file"], assets, "reference-" + entry["file"]))
                             for entry in result["references"] or ()], "sides": []}
    views = list(specs.module(name).CAMERAS)
    for side in result["sides"]:
        key = "%s-%02d" % (side["side"], side["trial"])
        final = Path(side.get("final_folder") or "/nonexistent")   # Absent when the side sent no message.
        capture = Path(side.get("capture_folder") or "/nonexistent")
        images["sides"].append({
            "label": side["label"], "verdict": side["verdict"],
            "harness": _copy(final / "harness-final.png", assets, key + "-harness-final.png"),
            "agent": _copy(final / "agent-final-preview.png", assets, key + "-agent-final.png"),
            "views": [(view, _copy(capture / ("render-%s.png" % view), assets, "%s-render-%s.png" % (key, view)))
                      for view in views]})
    return images


def html_page(result, images, markdown_text):
    def figure(source, caption):
        body = '<img src="%s" alt="%s">' % (html.escape(source), html.escape(caption)) if source \
            else '<div class="missing">none</div>'
        return "<figure>%s<figcaption>%s</figcaption></figure>" % (body, html.escape(caption))
    rows = ['<section><h3>References sent with the brief</h3><div class="row">%s</div></section>' % "".join(
        figure(source, "reference: " + view) for view, source in images["references"])]
    for side in images["sides"]:
        rows.append('<section><h3>%s: %s</h3><div class="row">%s%s</div><div class="row">%s</div></section>' % (
            html.escape(side["label"]), html.escape(side["verdict"]),
            figure(side["harness"], "final still, rendered by the harness with the scene's own settings"),
            figure(side["agent"], "the still the agent saved itself"),
            "".join(figure(source, "fixed view: " + view) for view, source in side["views"])))
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>One-shot test</title>
<style>
:root{--bg:#0f0e0d;--ink:#efe9df;--muted:#a59c8f;--line:#2b2724;--accent:#e7a35b}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1500px;margin:0 auto;padding:32px 16px 64px}
h1{font:600 34px/1.15 Georgia,serif;margin:0 0 6px}
h3{font:600 15px/1.3 sans-serif;color:var(--accent);margin:28px 0 10px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:12px}
figure{margin:0}img{width:100%;display:block;border-radius:6px;border:1px solid var(--line)}
figcaption{color:var(--muted);font-size:13px;margin-top:4px}
.missing{aspect-ratio:16/9;border:1px dashed var(--line);border-radius:6px;display:grid;place-items:center;
color:var(--muted)}
pre{white-space:pre-wrap;background:#161412;border:1px solid var(--line);border-radius:8px;padding:16px;
font:13px/1.45 ui-monospace,Menlo,monospace;color:var(--ink)}
</style></head><body><main>
<h1>@TITLE@</h1>
@ROWS@
<h3>Report</h3><pre>@BODY@</pre></main></body></html>
""".replace("@TITLE@", html.escape("One-shot test: " + result["title"])).replace("@ROWS@", "".join(rows)).replace(
        "@BODY@", html.escape(markdown_text))


def write(output, result):
    output = Path(output)
    text = markdown(result)
    (output / REPORT).write_text(text)
    (output / PAGE).write_text(html_page(result, gather_images(output, result), text))
    return output / REPORT


def dry_run_checks(output):
    """What the unbilled dry run must show on every side, beyond the scenario dry run's own checks."""
    result = json.loads((Path(output) / SCORECARD).read_text())
    still = bool(result.get("final_still"))   # A mini asks for none: its scripted agent neither renders nor looks.
    checks = {}
    for side in result["sides"]:
        key, path = "%s-%02d" % (side["side"], side["trial"]), side["path"] or {}
        checks[key + ": exactly one message was typed"] = path.get("messages_typed") == 1
        checks[key + ": passed (%s)" % ("closed checks, the saved still, pixel checks" if still
                                        else "closed checks")] = side["verdict"] == "passed"
        if still:
            checks[key + ": the final still was rendered and found"] = bool(side.get("agent_still")) and bool(
                (path.get("looked") or {}).get("rendered"))
            checks[key + ": the image files it wrote were recorded"] = result["final_still"]["name"] in [
                entry["file"] for entry in side.get("produced") or ()] and not side.get("still_missing")
            looked = (path.get("looked") or {}).get("images") or 0
            checks[key + ": looked at an image of its own result"] = looked >= 1
        checks[key + ": path metrics recorded"] = bool((path.get("tool_calls") or {}).get("total")) and (
            path.get("stop") or {}).get("reason") == "finished"
        if side["side"] == "angelo":
            checks[key + ": approvals counted and timed"] = (path["approvals"]["asked"] >= (2 if still else 1)
                                                             and path["approvals"]["wait_s"] is not None)
        elif side["runtime"] != "codex" and still:
            checks[key + ": the client could read the image it rendered"] = (
                path["approvals"]["denied_by_client"] == 0 and (path["looked"]["delivered_to_model"] or 0) >= 1)
    return checks
