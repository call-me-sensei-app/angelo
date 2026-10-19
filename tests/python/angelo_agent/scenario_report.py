# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The human report of a scenario run: ``SCENARIO-REPORT.md`` and ``scenario-report.html`` (host side).

Built only from ``scenario-scorecard.json`` and the run's meta data, so it can
be rebuilt at any time (``bench_mcp_vs_angelo.py --summarize-only``). Images
are copied into ``report-assets/`` with relative links, so the run folder can
be archived or shared as it is. "Where each side struggled" lists every
failed closed check with its measured values; a person adds the judgement.
A turn no check graded is shown as UNGRADED, and a turn whose only unmet
checks could not be decided as INCONCLUSIVE; each is counted apart from passed
and failed turns and never as passed (``eval_grading``). "Every turn passed" is
said only when each did.
"""

import html
import json
import shutil
from pathlib import Path

import bench_integrity
import scenario_specs as specs

LABELS = {"angelo": "Angelo", "mcp": "Blender MCP"}
RUNTIMES = {"claude": "Claude Code", "codex": "Codex"}


def side_label(trial):
    """``Angelo``, or ``Blender MCP (Claude Code)``/``(Codex)``: a dry run has both MCP runtimes."""
    runtime = RUNTIMES.get(trial.get("runtime"))
    return "%s (%s)" % (LABELS[trial["side"]], runtime) if trial["side"] == "mcp" and runtime else LABELS[trial["side"]]


def cell(value):
    return "–" if value is None else str(value)
METRIC_ROWS = (
    ("Turns passed / failed / inconclusive / ungraded / not attempted (closed checks)",
     lambda m, s: "%s / %s / %s / %s / %s of %s" % (
         m.get("turns_passed"), m.get("turns_failed"), m.get("turns_inconclusive", 0), m.get("turns_ungraded"),
         m["turns_total"] - m["turns_attempted"], m["turns_total"])),
    ("Turns completed / timed out", lambda m, s: "%s / %s" % (m.get("turns_completed"), m.get("turns_timed_out"))),
    ("Wall time, s (all turns)", lambda m, s: m.get("wall_s")),
    ("Cost, USD (basis below)", lambda m, s: m.get("cost_compared_usd")),
    ("Input tokens (incl. cache) / output", lambda m, s: "%s / %s" % (m.get("input_tokens"), m.get("output_tokens"))),
    ("Model calls (MCP API calls; Angelo rounds)", lambda m, s: m.get("api_calls") if s["side"] == "mcp"
     else m.get("model_rounds") if m.get("model_rounds") is not None else m.get("provider_turn_requests")),
    ("Python executed (raw on MCP; approved reviewed on Angelo)", lambda m, s: m.get("python_executions")),
    ("Approvals requested / approved / declined", lambda m, s: "%s / %s / %s" % (
        m["approvals"].get("requested"), m["approvals"].get("approved"), m["approvals"].get("declined"))),
    ("Undo steps in the session", lambda m, s: (s.get("undo") or {}).get("undo_steps")),
    ("Turn boundaries reachable by Undo", lambda m, s: "%s / %s" % ((s.get("undo") or {}).get("turn_boundaries_reachable"),
                                                                    (s.get("undo") or {}).get("turns"))),
    ("One Undo returns exactly one turn", lambda m, s: (s.get("undo") or {}).get("first_undo_returns_previous_turn")),
    ("Errors", lambda m, s: _errors(m["errors"])),
    ("Scene hygiene issues", lambda m, s: (s.get("hygiene") or {}).get("issues")),
    ("New orphan data-blocks (dropped on save)", lambda m, s: (s.get("hygiene") or {}).get("new_orphan_count")),
    ("End-of-session compliance (all checks re-run): passed / failed / inconclusive of all",
     lambda m, s: compliance(s.get("end_of_session_checks"))),
    ("Silhouette IoU, mean of views", lambda m, s: _mean(s, "scene_iou")),
    ("Subject IoU, mean of views", lambda m, s: _mean(s, "subject_iou")),
    ("Depth agreement (within 5 %), mean", lambda m, s: _mean(s, "depth_agreement")),
    ("Depth error, median relative, mean of views", lambda m, s: _mean(s, "depth_rel_median")),
)


def compliance(checks):
    """``12 / 1 / 1 of 14``: the saved scene's checks passed, failed and inconclusive, of all of them."""
    if not checks:
        return None
    undecided = checks.get("inconclusive") if isinstance(checks.get("inconclusive"), int) else 0
    failed = checks.get("failed_count", checks["total"] - checks["passed"] - undecided)
    return "%s / %s / %s of %s" % (checks["passed"], failed, undecided, checks["total"])


def turn_label(turn):
    """``pass``, ``FAIL``, ``INCONCLUSIVE`` (only undecided checks unmet) or ``UNGRADED`` (no check read it)."""
    if turn["passed"]:
        return "pass"
    return {"ungraded": "UNGRADED", "inconclusive": "INCONCLUSIVE"}.get(turn.get("grade"), "FAIL")


def _mean(side, metric):
    scores = side.get("scores") or {}
    return (scores.get("mean") or {}).get(metric) if isinstance(scores, dict) else None


def _errors(errors):
    parts = ["%s %s" % (key.replace("_", " "), value) for key, value in errors.items()
             if value not in (None, 0, False, {}, [], "")]
    return ", ".join(parts) or "none"


def _copy(source, assets, name):
    if not source or not Path(source).is_file():
        return None
    assets.mkdir(exist_ok=True)
    shutil.copyfile(source, assets / name)
    return "report-assets/" + name


def gather_demos(output, card):
    """``[(title, video, poster)]`` relative paths of each Angelo trial's demo recording."""
    assets, demos = Path(output) / "report-assets", []
    for trial in card["trials"]:
        demo = trial.get("demo") or {}
        if demo.get("video"):
            key = "%s-%02d" % (trial["side"], trial["trial"])
            demos.append(("%s, trial %d" % (side_label(trial), trial["trial"]),
                          _copy(demo["video"], assets, key + "-demo-1080p.mp4"),
                          _copy(demo.get("poster"), assets, key + "-poster-1080p.png")))
    return demos


def gather_images(output, card):
    """Relative paths per view: the reference, each trial's render and silhouette overlap."""
    assets = Path(output) / "report-assets"
    folder = specs.references_dir(card["scenario"])
    images = {}
    for view, entry in ((card.get("references") or {}).get("images") or {}).items():
        images.setdefault(view, {})["reference"] = _copy(folder / entry["file"], assets,
                                                         "reference-%s%s" % (view, Path(entry["file"]).suffix))
    for trial in card["trials"]:
        capture = Path(trial.get("capture_folder") or "/nonexistent")
        key = "%s-%02d" % (trial["side"], trial["trial"])
        for view in images:
            images[view][key] = _copy(capture / ("render-%s.png" % view), assets, "%s-render-%s.png" % (key, view))
            images[view][key + "-silhouette"] = _copy(capture / ("silhouette-%s.png" % view), assets,
                                                      "%s-silhouette-%s.png" % (key, view))
    return images


def struggles(card):
    lines = []
    for trial in card["trials"]:
        label = "%s trial %d" % (side_label(trial), trial["trial"])
        unmet = [(turn["turn"], check) for turn in trial["turns"] for check in turn["failed_checks"]]
        failed = [item for item in unmet if not item[1].get("inconclusive")]
        undecided = [item for item in unmet if item[1].get("inconclusive")]
        timed_out = [turn["turn"] for turn in trial["turns"] if turn["status"] == "timeout"]
        ungraded = [turn["turn"] for turn in trial["turns"] if turn.get("grade") == "ungraded"]
        # Not passed, yet no failed check and no timeout to show for it (a stopped or errored turn).
        unexplained = [turn["turn"] for turn in trial["turns"] if turn.get("passed") is False
                       and not turn["failed_checks"] and turn["turn"] not in timed_out + ungraded]
        if not unmet and not timed_out and not ungraded and not unexplained:
            lines.append("- **%s**: every turn passed its checks." % label)
            continue
        lines.append("- **%s**: %d failed check(s), %d inconclusive%s%s%s." % (
            label, len(failed), len(undecided), ", timed out on " + ", ".join(timed_out) if timed_out else "",
            ", ungraded (no check ran) on " + ", ".join(ungraded) if ungraded else "",
            ", not passed without a recorded check on " + ", ".join(unexplained) if unexplained else ""))
        for turn, check in unmet[:30]:
            details = json.dumps(check.get("details"), default=str)
            mark = " (inconclusive)" if check.get("inconclusive") else ""
            lines.append("  - `%s` / %s%s: %s" % (turn, check.get("label"), mark, details[:240]))
    return lines


def markdown(card, meta, images, demos=()):
    trials = card["trials"]
    plan = card["plan"]
    run = meta.get("plan") or {}
    model = "scripted models (unbilled dry run)" if meta.get("dry_run_scenario") else "model %s, runtime %s, effort %s" % (
        run.get("model"), run.get("runtime"), run.get("effort"))
    head = ["# Scenario benchmark: %s" % plan["title"], "",
            "Scenario `%s`, %d turns and %d closed checks; references `%s` (ground-truth digest %s); %s; "
            "finished %s." % (card["scenario"], len(plan["turns"]), plan["checks"], plan["references"],
                              plan["reference_digest"], model, meta.get("finished_at")), "",
            *bench_integrity.markdown_lines(meta.get("conditions"))]   # Scripts and planned n.
    if run.get("demo_record"):
        head += ["Angelo's window was recorded for demo videos (`--demo-record`): each frame grab redraws the "
                 "window on the main thread, so Angelo's timings here are not comparable with unrecorded runs.", ""]
    columns = ["%s %d" % (side_label(t), t["trial"]) for t in trials]
    head += ["## Per-side metrics", "", "| Metric | " + " | ".join(columns) + " |",
             "| --- |" + " --- |" * len(columns)]
    for label, value in METRIC_ROWS:
        cells = []
        for trial in trials:
            try:
                cells.append(cell(value(trial["metrics"], trial)))
            except (KeyError, TypeError):
                cells.append("–")
        head.append("| %s | %s |" % (label, " | ".join(cells)))
    head += ["", "Cost basis, the same for every side and trial: %s." % (card.get("cost_basis") or {}).get(
        "label", "not recorded")]
    head += ["", "## Turns", "", "| Turn | " + " | ".join(columns) + " |", "| --- |" + " --- |" * len(columns)]
    for turn_id in plan["turns"]:
        cells = []
        for trial in trials:
            turn = next((t for t in trial["turns"] if t["turn"] == turn_id), None)
            seconds = "" if turn is None or turn["status"] == "skipped" else ", time unknown" if turn.get(
                "wall_s") is None else ", %.0f s" % turn["wall_s"]
            cells.append("–" if turn is None else "%s %s%s" % (turn_label(turn), turn["status"], seconds))
        head.append("| %s | %s |" % (turn_id, " | ".join(cells)))
    head += ["", "## Views (silhouette IoU / subject IoU / depth agreement)", "",
             "| View | " + " | ".join(columns) + " |", "| --- |" + " --- |" * len(columns)]
    for view in images:
        cells = []
        for trial in trials:
            entry = ((trial.get("scores") or {}).get("views") or {}).get(view) if isinstance(trial.get("scores"), dict) else None
            cells.append("–" if not entry else "%.3f / %.3f / %.3f" % (entry["scene_iou"], entry["subject_iou"],
                                                                     entry["depth_agreement"]))
        head.append("| %s | %s |" % (view, " | ".join(cells)))
    head += ["", "Images: `scenario-report.html` shows each view side by side (reference, then each trial).", ""]
    if demos:
        head += ["## Demo recordings", ""] + ["- %s: `%s`, poster `%s`" % demo for demo in demos] + [""]
    head += ["## Where each side struggled", ""] + struggles(card)
    head += ["", "## Method", "",
             "- Both sides receive the same prompts and the same reference JPEGs (SHA-256 checked before each send): "
             "Angelo as chat attachments, Claude Code as base64 image blocks, Codex as local images.",
             "- Each turn is graded after it ends by closed host checks derived from the ground-truth scene "
             "(`scenario_checks`), never by the model.",
             "- At the end each side's scene is saved and captured in the same stock Blender as the ground truth: "
             "one ray per pixel from each fixed camera (`scenario_capture`), scored by `scenario_scoring`.",
             "- Review renders use the same camera, samples and colour management for all three columns.",
             "- The Undo probe runs last: it presses Undo back to the start and reports the turn boundaries reached.",
             ""]
    return "\n".join(head) + "\n"


def html_page(card, meta, images, markdown_text, demos=()):
    trials = card["trials"]
    keys = ["%s-%02d" % (t["side"], t["trial"]) for t in trials]
    titles = ["%s, trial %d" % (side_label(t), t["trial"]) for t in trials]
    rows = []
    for view, entry in images.items():
        cells = ['<figure><img src="%s" alt="reference %s"><figcaption>Reference</figcaption></figure>'
                 % (html.escape(entry.get("reference") or ""), html.escape(view))]
        for key, title in zip(keys, titles):
            render, silhouette = entry.get(key), entry.get(key + "-silhouette")
            cells.append('<figure>%s%s<figcaption>%s</figcaption></figure>' % (
                '<img src="%s" alt="%s %s">' % (html.escape(render), html.escape(title), html.escape(view)) if render
                else '<div class="missing">no render</div>',
                '<img class="sil" src="%s" alt="silhouette overlap">' % html.escape(silhouette) if silhouette else "",
                html.escape(title)))
        rows.append('<section><h3>%s</h3><div class="row" style="--n:%d">%s</div></section>'
                    % (html.escape(view.replace("_", " ")), 1 + len(keys), "".join(cells)))
    for title, video, poster in demos:
        rows.insert(0, '<section><h3>Demo recording: %s</h3><video controls muted playsinline preload="metadata" '
                       'poster="%s" src="%s"></video></section>' % (html.escape(title), html.escape(poster or ""),
                                                                     html.escape(video or "")))
    body = html.escape(markdown_text)
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scenario benchmark</title>
<style>
:root{--bg:#0f0e0d;--ink:#efe9df;--muted:#a59c8f;--line:#2b2724;--accent:#e7a35b}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1500px;margin:0 auto;padding:32px 16px 64px}
h1{font:600 34px/1.15 Georgia,serif;margin:0 0 6px}h3{font:600 15px/1.3 sans-serif;color:var(--accent);
text-transform:capitalize;margin:28px 0 10px}
.row{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:12px}
figure{margin:0}img,video{width:100%;display:block;border-radius:6px;border:1px solid var(--line)}
video{max-width:960px;background:#000}
img.sil{margin-top:6px;opacity:.9}figcaption{color:var(--muted);font-size:13px;margin-top:4px}
.missing{aspect-ratio:16/9;border:1px dashed var(--line);border-radius:6px;display:grid;place-items:center;color:var(--muted)}
pre{white-space:pre-wrap;background:#161412;border:1px solid var(--line);border-radius:8px;padding:16px;
font:13px/1.45 ui-monospace,Menlo,monospace;color:var(--ink)}
.key{color:var(--muted);font-size:13px}
@media (max-width:800px){.row{grid-template-columns:1fr}}
</style></head><body><main>
<h1>@TITLE@</h1><p class="key">Silhouette overlap under each render: white both, orange reference only, blue that side only.</p>
@ROWS@
<h3>Report</h3><pre>@BODY@</pre></main></body></html>
""".replace("@TITLE@", html.escape(card["plan"]["title"])).replace("@ROWS@", "".join(rows)).replace("@BODY@", body)


def write(output, card, meta):
    output = Path(output)
    images, demos = gather_images(output, card), gather_demos(output, card)
    text = markdown(card, meta, images, demos)
    (output / "SCENARIO-REPORT.md").write_text(text)
    (output / "scenario-report.html").write_text(html_page(card, meta, images, text, demos))
    return output / "SCENARIO-REPORT.md"
