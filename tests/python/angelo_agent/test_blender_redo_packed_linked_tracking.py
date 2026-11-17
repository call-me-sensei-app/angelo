# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Redo after an Undo of packed linked data never crashes (BREAKIT-C7A-13 M1, fixed in Angelo's 5.3 for 2.0.0:
bugfix/v2.0.0-linked-packed-redo-crash; doc/angelo/agent_integration.rst, the merge's privacy).

Runs ``redo_packed_linked_repro.py`` in the Angelo under test with Angelo's add-on disabled: a node group packed from
one library, used by a collection the person links from another, linked, Undo, Redo. Plain 5.3 crashed on the packed
case; both it and the unpacked control must now survive. A regression check: red if either crashes again. The
parallel merge still refuses such data in 2.0.0 (defense in depth); lifting that is on the 2.1.0 backlog. HOME is
private to each run. Standard library only.
"""
import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPRO = Path(__file__).resolve().parent / "redo_packed_linked_repro.py"


def run(binary, out, packed):
    folder = out / ("packed" if packed else "unpacked")
    folder.mkdir(parents=True, exist_ok=True)
    # Its own temporary folder outside the suite's: a crash (a regression) writes ``<temp>/*.crash.txt``, which the
    # suite runner fails any run for leaving in its folder; it is kept renamed, for a look.
    tmp = Path(tempfile.mkdtemp(prefix="redo-tracking-", dir="/private/tmp" if sys.platform == "darwin" else None))
    try:
        env = dict(os.environ, HOME=str(folder / "home"), TMPDIR=str(tmp) + "/", TEMP=str(tmp), TMP=str(tmp))
        command = [binary, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(REPRO),
                   "--", str(folder / "out")] + ([] if packed else ["unpacked"])
        done = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace", timeout=600)
        for log in tmp.rglob("*.crash.txt"):   # Kept under another name, for a look.
            shutil.copyfile(log, folder / (log.name[:-len(".crash.txt")] + "-crash-log.txt"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    (folder / "log.txt").write_text(done.stdout + done.stderr, encoding="utf-8")
    survived = [line for line in done.stdout.splitlines() if line.startswith("REDO_SURVIVED ")]
    return {"returncode": done.returncode, "crashed": done.returncode < 0 or done.returncode >= 128,
            "survived": json.loads(survived[-1].split(" ", 1)[1]) if survived else None}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, help="the Angelo under test")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    packed, unpacked = run(args.binary, out, True), run(args.binary, out, False)
    checks = {
        "packed_redo_survives": packed["survived"] is not None and not packed["crashed"],
        "unpacked_control_survives": unpacked["survived"] is not None and not unpacked["crashed"],
    }
    report = {"success": all(checks.values()), "checks": checks, "packed": packed, "unpacked": unpacked}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ANGELO_REDO_PACKED_LINKED_TRACKING", json.dumps(report))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
