# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Kill, quit or fill the disk under a running animation render, then relaunch twice (VFX audit A1).

Runs ``blender_kill_recovery.py`` in the real app, each launch with the same
private state folder and workspace (so the next start sees what the last one
left), and checks after every scenario:

* every finished frame on disk when the app died is published (at most the one
  being written then is dropped), and Blender itself reads every published file
  as a whole image;
* nothing hidden is left behind;
* "Render the missing frames" completes the sequence in the same folder;
* a further start changes nothing.

Scenarios (``--scenario``, repeatable; all by default):

* ``kill-render``: ``kill -9`` once 8 frames are done (PNG).
* ``kill-write``: ``kill -9`` while Blender is writing a large OpenEXR frame (the
  driver polls the staging folder every 2 ms for a file still growing).
* ``quit``: the app quits by itself after 6 frames (OpenEXR).
* ``disk-full``: the workspace is a small disk image that fills mid-render
  (macOS only; ``hdiutil``).

    python3 tests/python/angelo_agent/kill_recovery_driver.py --binary <Angelo> --out /tmp/angelo-kill-qa

Writes ``<out>/kill-recovery.json`` with every scenario's facts and verdict.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import gui_launch  # noqa: E402
import run_native_suites  # noqa: E402

SCRIPT = HERE / "blender_kill_recovery.py"
SCENARIOS = ("kill-render", "kill-write", "quit", "disk-full")
SUITE = SimpleNamespace(state_in_qa=False, default_chat=False, first_launch=False, report=None, options_env=(),
                        kind="gui", live_env=())


def launch(binary, run_dir, env, phase, extra):
    command = [str(binary), "--factory-startup", "--python-exit-code", "1"]
    command += gui_launch.window_flags(input_isolation=True) + ["--python", str(SCRIPT)]
    child_env = dict(env, ANGELO_KILL_PHASE=phase, **extra)
    log = open(run_dir / ("launch-%s.log" % phase), "a", encoding="utf-8")
    return subprocess.Popen(command, env=child_env, stdout=log, stderr=subprocess.STDOUT)


def report(qa, phase):
    try:
        return json.loads((qa / ("kill-%s.json" % phase)).read_text())
    except (OSError, ValueError):
        return {}


def wait(process, seconds):
    try:
        return process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        return "timeout"


def staged_files(qa):
    folder = Path(os.environ.get("ANGELO_KILL_WORKSPACE", str(qa / "workspace"))) / "renders"
    return [path for path in folder.glob(".angelo-job-*/*") if path.name != ".angelo-job"] if folder.is_dir() else []


def kill_when(process, qa, condition, limit):
    """SIGKILL the app the moment ``condition(qa)`` holds; returns what was staged then."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline and process.poll() is None:
        seen = condition(qa)
        if seen:
            os.kill(process.pid, signal.SIGKILL)
            process.wait()
            return seen
        time.sleep(0.002)
    process.kill()
    process.wait()
    return None


def frames_done(qa, count):
    def condition(_qa):
        status = report(qa, "render")
        return {"frames_done": status["frames_done"], "staged": status.get("staged")} \
            if status.get("frames_done", 0) >= count else None
    return condition


def growing_exr(qa, after=3):
    """A frame file still being written, once ``after`` frames are whole before it (so a kill tears a later one)."""
    sizes = {}

    def condition(_qa):
        files = staged_files(qa)
        if len(files) <= after:
            for path in files:
                try:
                    sizes[path.name] = path.stat().st_size
                except OSError:
                    pass
            return None
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if path.name in sizes and 0 < sizes[path.name] < size:   # Still being written.
                return {"torn_candidate": path.name, "size": size,
                        "staged": {item.name: item.stat().st_size for item in staged_files(qa)}}
            sizes[path.name] = size
        return None
    return condition


def verdict(name, staged_at_end, check, again, frames, at_kill=None):
    published = check.get("published", {})
    mains = sorted(name for name in published if name.rsplit("/", 1)[-1].startswith("frame_"))
    staged_mains = sorted(name for name in (staged_at_end or {}) if name.startswith("frame_"))
    record = next((value for value in check.get("after_start", {}).values() if value["kind"] == "RENDER_ANIMATION"),
                  {})
    resumed = check.get("published_after_resume", {})
    facts = {
        "no_error": not check.get("error") and not again.get("error"),
        "kept_every_finished_frame": len(staged_mains) - 1 <= len(mains) <= len(staged_mains),
        "every_published_file_reads_whole": bool(published) and all(check.get("blender_reads", {}).values()),
        "nothing_hidden_left": not check.get("hidden_left"),
        "record_says_what_was_kept": record.get("receipt", {}).get("kept") == "%d/%d frames" % (len(mains), frames),
    }
    if name == "disk-full":   # The disk stays full: no rest; what matters is what was kept.
        facts["another_start_changes_nothing"] = again.get("published") == published
    else:
        facts.update({
            "rest_completes_the_sequence": (check.get("resume_record", {}).get("status") == "done"
                                            and sum(1 for name in resumed if "frame_" in name) == frames
                                            and all(check.get("blender_reads_after_resume", {}).values())),
            "kept_frames_untouched_by_the_rest": all(resumed.get(name) == size for name, size in published.items()),
            "another_start_changes_nothing": again.get("published") == resumed})
    if name == "kill-write":
        torn = (at_kill or {}).get("torn_candidate")
        facts["a_torn_file_was_caught"] = bool(torn) and bool(staged_at_end) and any(
            size < max(staged_at_end.values()) for size in staged_at_end.values())
        facts["the_torn_frame_was_not_kept"] = bool(torn) and not any(
            name.endswith("/" + torn) and size == staged_at_end.get(torn) for name, size in published.items())
    if name == "disk-full":
        # The render stops at the full disk; its end keeps the whole frames (the torn one dropped) and, with no room
        # even for a folder, keeps them staged and says so. Once there is room, the next start publishes them.
        receipt = record.get("receipt", {})
        kept = receipt.get("frames_kept", 0)
        later = again.get("published", {})
        later_mains = [name for name in later if name.rsplit("/", 1)[-1].startswith("frame_")]
        whole_at_end = [name for name in staged_mains if staged_at_end[name] >= 0.9 * max(staged_at_end.values())]
        facts = {
            "no_error": not check.get("error") and not again.get("error"),
            "kept_every_finished_frame": 0 < kept == len(whole_at_end) < frames,
            "record_says_what_was_kept": receipt.get("kept") == "%d/%d frames" % (kept, frames)
            and receipt.get("torn") == 1,
            "frames_published_once_there_is_room": len(later_mains) == kept,
            "every_published_file_reads_whole": bool(later) and all(again.get("blender_reads", {}).values()),
            "nothing_hidden_left_then": not again.get("hidden_left"),
        }
    return facts


def run(name, binary, out, attempts=12):
    run_dir = out / name
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    mount = None
    if name == "disk-full":
        image = run_dir / "small.dmg"
        subprocess.run(["hdiutil", "create", "-size", "24m", "-fs", "HFS+", "-volname", "AngeloKillQA", str(image)],
                       check=True, capture_output=True)
        mount = run_dir / "mnt"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)], check=True,
                       capture_output=True)
        if not os.path.ismount(mount):   # Never fill the main disk because an attach failed.
            raise RuntimeError("The disk image did not mount at %s" % mount)
        # Leave room for the scene and a few frames only, so the render runs out of space part way.
        room = shutil.disk_usage(mount).free - 7 * 1024 * 1024
        with open(mount / "filler.bin", "wb") as handle:
            handle.truncate(0)
            handle.write(b"\0" * max(0, room))
    try:
        qa = run_dir / "qa"   # Reports and state on the main disk; only the workspace may be on the image.
        qa.mkdir(parents=True, exist_ok=True)
        env = run_native_suites.build_environment(SUITE, run_dir / "env", binary, dict(os.environ),
                                                  run_dir / "suite.log")
        env["ANGELO_KILL_DIR"] = str(qa)
        workspace = (mount / "workspace") if mount else (qa / "workspace")
        env["ANGELO_KILL_WORKSPACE"] = os.environ["ANGELO_KILL_WORKSPACE"] = str(workspace)
        frames = 24
        extra = {"ANGELO_KILL_FRAMES": str(frames)}
        if name == "kill-render":
            extra.update(ANGELO_KILL_FORMAT="PNG")
            process = launch(binary, run_dir, env, "render", extra)
            at_kill = kill_when(process, qa, frames_done(qa, 8), 300)
        elif name == "kill-write":
            extra.update(ANGELO_KILL_FORMAT="OPEN_EXR", ANGELO_KILL_SIZE="1536", ANGELO_KILL_SAMPLES="1")
            at_kill = None
            for _ in range(attempts):   # Until a kill lands while a frame is being written.
                shutil.rmtree(qa, ignore_errors=True)
                qa.mkdir(parents=True)
                shutil.rmtree(Path(env["ANGELO_STATE_DIR"]), ignore_errors=True)
                Path(env["ANGELO_STATE_DIR"]).mkdir(parents=True)
                process = launch(binary, run_dir, env, "render", extra)
                at_kill = kill_when(process, qa, growing_exr(qa), 300)
                if at_kill and at_kill.get("torn_candidate"):
                    break
        elif name == "quit":
            extra.update(ANGELO_KILL_FORMAT="OPEN_EXR", ANGELO_KILL_QUIT_AT="6")
            process = launch(binary, run_dir, env, "render", extra)
            at_kill = {"exit": wait(process, 300), "staged": report(qa, "render").get("staged")}
        else:
            extra.update(ANGELO_KILL_FORMAT="OPEN_EXR", ANGELO_KILL_SIZE="1024", ANGELO_KILL_SAMPLES="1",
                         ANGELO_KILL_NO_RESUME="1")
            process = launch(binary, run_dir, env, "render", extra)
            at_kill = {"exit": wait(process, 300), "staged": report(qa, "render").get("staged"),
                       "record": report(qa, "render").get("record")}
        staged = (at_kill or {}).get("staged") or {}
        check = (wait(launch(binary, run_dir, env, "check", extra), 300), report(qa, "check"))[1]
        if mount is not None:   # The disk stays full at the first start: the frames wait; free room for the next.
            (mount / "filler.bin").unlink(missing_ok=True)
        again = (wait(launch(binary, run_dir, env, "again", extra), 120), report(qa, "again"))[1]
        facts = verdict(name, staged, check, again, frames, at_kill)
        return {"scenario": name, "at_kill": at_kill, "check": check, "again": again, "facts": facts,
                "passed": all(facts.values())}
    finally:
        if mount is not None:
            subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("/tmp/angelo-kill-qa"))
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    results = [run(name, args.binary.resolve(), args.out) for name in args.scenario or SCENARIOS]
    (args.out / "kill-recovery.json").write_text(json.dumps(results, indent=2, default=str))
    for result in results:
        print(result["scenario"], "PASS" if result["passed"] else "FAIL",
              {key: value for key, value in result["facts"].items() if not value})
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
