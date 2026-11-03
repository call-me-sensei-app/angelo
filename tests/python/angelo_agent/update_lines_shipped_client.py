# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release gate: a feed with ``lines`` read by published Angelo apps, unchanged, over real HTTP.

    python3 update_lines_shipped_client.py --app /path/to/Angelo.app [--app ...] --later 1.0.10 --line 1.1.0 --out DIR

Each app runs its own bundled code in the background (``update_lines_probe.py``) against a loopback server that serves a
feed signed with a throwaway key; the app's own key list is not touched, the key reaches only the probe's ``Checker``.
Three feeds, each read by every app:

* ``lines``: ``latest`` a later release of the app's line, a newer ``recommended`` line in ``lines``. Pass: the ordinary
  update to ``latest``, nothing of the newer line anywhere, Angelo may install it itself.
* ``current``: ``latest`` the app's own version, the same line. Pass: up to date, no failure, so a person on
  the newest build before 1.0.9 hears nothing they can't act on.
* ``latest_is_the_line`` (the design this replaced): ``latest`` the newer line with ``automatic_install`` false. Recorded
  to show what that feed does: a download offer only, and no route to a later release of the app's own line.

Contract: doc/angelo/updates_and_reports.rst, "Release lines". Exit 0 when every app passes.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_fixtures as ufx  # noqa: E402
from loopback_server import LoopbackServer  # noqa: E402
from update_e2e_common import DOWNLOADS, isolated_environment  # noqa: E402

PLATFORM = "macos-arm64"


def release(version, **changes):
    item = ufx.payload(version=version)["latest"]
    item["downloads"] = {PLATFORM: {"url": "%s/%s/Angelo-%s-macos-arm64.dmg" % (DOWNLOADS, version, version),
                                    "sha256": "a" * 64, "size": 460_000_000}}
    item.update(changes)
    return item


def feeds(own, later, line):
    return {
        "lines": ufx.payload(version=later, latest=release(later), lines=[release(line, recommended=True)], sequence=11),
        "current": ufx.payload(version=own, latest=release(own), lines=[release(line, recommended=True)], sequence=12),
        "latest_is_the_line": ufx.payload(version=line, latest=release(line, automatic_install=False), sequence=13),
    }


def own_version(app):
    plist = Path(app) / "Contents/Info.plist"
    return subprocess.run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(plist)],
                          capture_output=True, text=True, check=True).stdout.strip()


def read_with(app, name, document, seed, keys, work):
    with LoopbackServer() as server:
        server.route("/updates/v1/stable.json", body=ufx.envelope(document, seed),
                     headers={"ETag": '"%s"' % name, "Content-Type": "application/json"})
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BLENDER_", "ANGELO_"))}
        env.update(isolated_environment(work), HOME=str(Path(work) / "home"), ANGELO_DISABLE_HERO="1")
        env.update(ANGELO_UPDATE_FEED_URL=server.base + "/updates/v1/stable.json",
                   ANGELO_LINES_KEY=base64.b64encode(keys[ufx.KEY_ID]).decode(), ANGELO_LINES_KEY_ID=ufx.KEY_ID,
                   ANGELO_LINES_OUT=str(Path(work) / "probe.json"))
        done = subprocess.run([str(Path(app) / "Contents/MacOS/Angelo"), "--background", "--factory-startup", "--python",
                               str(HERE / "update_lines_probe.py")], env=env, capture_output=True, text=True, timeout=300)
        path = Path(work) / "probe.json"
        result = json.loads(path.read_text()) if path.exists() else {"error": done.stderr[-800:] + done.stdout[-800:]}
        result["requests"] = server.count("/updates/v1/stable.json")
        return result


def judge(name, result, own, later, line):
    if name == "lines":
        return (result.get("kind") == "update" and result.get("offered") == later and result.get("automatic_install") is True
                and result.get("critical") is False and line not in json.dumps(result.get("feed_read")))
    if name == "current":
        return result.get("kind") == "current" and not result.get("detail")
    return result.get("kind") == "update" and result.get("offered") == line and result.get("automatic_install") is False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", action="append", required=True)
    parser.add_argument("--later", default="1.0.10")
    parser.add_argument("--line", default="1.1.0")
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    seed, keys = ufx.new_key()
    report, passed = [], True
    for app in arguments.app:
        own = own_version(app)
        for name, document in feeds(own, arguments.later, arguments.line).items():
            with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as work:
                result = read_with(app, name, document, seed, keys, work)
            ok = judge(name, result, own, arguments.later, arguments.line)
            passed = passed and ok
            report.append({"app": own, "feed": name, "pass": ok, "result": result})
            print("%s %s %s: %s" % ("PASS" if ok else "FAIL", own, name, json.dumps(
                {k: result.get(k) for k in ("kind", "detail", "offered", "automatic_install", "critical", "error")})))
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "shipped-client.json").write_text(json.dumps({"success": passed, "runs": report}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
