# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the end-to-end update runs share (update_e2e_macos.py, update_e2e_windows.py): the report, the isolated
profile, the signed feed and the loopback server that serves it and the files it names."""

import base64
import hashlib
import json
import time
from pathlib import Path

DOWNLOADS = "https://downloads.angelo.studio"


class Report:
    """``<work>/qa/update-e2e.json``, written after every step so a run that stops still says how far it got."""

    def __init__(self, path):
        self.path, self.data = Path(path), {"checks": {}, "events": []}

    def event(self, text):
        self.data["events"].append([round(time.time(), 1), text])
        print("update-e2e:", text, flush=True)
        self.save()

    def check(self, name, value, detail=""):
        self.data["checks"][name] = bool(value)
        self.event("%s %s %s" % ("PASS" if value else "FAIL", name, detail))
        return value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str))

    def finish(self):
        self.data["success"] = bool(self.data["checks"]) and all(self.data["checks"].values())
        self.save()
        return self.data["success"]


def isolated_environment(work):
    """Angelo's and Blender's folders inside ``work``, the notices on, everything that would talk to a person's
    accounts off. The relaunched app gets these back from the installer (``ANGELO_*``, ``BLENDER_*``)."""
    work = Path(work)
    paths = {name: work / name for name in ("home", "tmp", "state", "connections", "library", "qa")}
    user = work / "blender-user"
    for path in list(paths.values()) + [user / part for part in ("config", "scripts", "datafiles", "extensions")]:
        path.mkdir(parents=True, exist_ok=True)
    return {
        # The temporary folder: the crash scan reads it, where other runs' crash logs would show their dialog.
        "TMPDIR": str(paths["tmp"]), "TMP": str(paths["tmp"]), "TEMP": str(paths["tmp"]),
        "BLENDER_USER_RESOURCES": str(user), "BLENDER_USER_CONFIG": str(user / "config"),
        "BLENDER_USER_SCRIPTS": str(user / "scripts"), "BLENDER_USER_DATAFILES": str(user / "datafiles"),
        "BLENDER_USER_EXTENSIONS": str(user / "extensions"),
        "ANGELO_STATE_DIR": str(paths["state"]), "ANGELO_CONNECTIONS_DIR": str(paths["connections"]),
        "ANGELO_LIBRARY_DIR": str(paths["library"]), "ANGELO_QA_DIR": str(paths["qa"]),
        "ANGELO_DISABLE_KEYCHAIN": "1", "ANGELO_DISABLE_INTRO": "1", "ANGELO_DISABLE_HERO": "1",
        "ANGELO_ENABLE_APP_NOTICES": "1", "ANGELO_AGENT_RUNTIME": "off",
        "ANGELO_NOTICES_DELAY": "0", "ANGELO_UPDATE_CHECK_DELAY": "0",
    }


def wait_for(condition, seconds, step=0.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = condition()
        if value:
            return value
        time.sleep(step)
    return condition()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def serve_update(server, platform, version, artifact, delta=None, from_version=None):
    """Serve a feed signed with a throwaway key announcing ``artifact`` (and ``delta``) at their published
    addresses, and the files themselves; returns the environment the old app and its driver need."""
    import update_fixtures as ufx
    seed, keys = ufx.new_key()
    document = ufx.payload(version=version, sequence=int(time.time()))
    entry = {"url": "%s/%s/%s" % (DOWNLOADS, version, Path(artifact).name), "sha256": sha256(artifact),
             "size": Path(artifact).stat().st_size}
    if delta:
        entry["deltas"] = [{"from": from_version, "format": "angelo-bundle-delta-1",
                            "url": "%s/%s/%s" % (DOWNLOADS, version, Path(delta).name), "sha256": sha256(delta),
                            "size": Path(delta).stat().st_size}]
    document["latest"]["downloads"] = {platform: entry}
    document["issued"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    document["expires"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400))
    server.route("/updates/v1/stable.json", body=ufx.envelope(document, seed),
                 headers={"ETag": '"e2e"', "Content-Type": "application/json"})
    server.serve_file("/%s/%s" % (version, Path(artifact).name), Path(artifact).read_bytes())
    if delta:
        server.serve_file("/%s/%s" % (version, Path(delta).name), Path(delta).read_bytes())
    return {"ANGELO_UPDATE_FEED_URL": server.base + "/updates/v1/stable.json", "ANGELO_E2E_DOWNLOADS": server.base,
            "ANGELO_E2E_KEY": base64.b64encode(keys[ufx.KEY_ID]).decode(), "ANGELO_E2E_KEY_ID": ufx.KEY_ID}


def driver_report(work):
    path = Path(work) / "qa" / "update-e2e-driver.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def last_seen(state_dir):
    try:
        return json.loads((Path(state_dir) / "updates.json").read_text()).get("last_seen_version", "")
    except (OSError, ValueError):
        return ""


def first_recent_file(config_dir):
    try:
        lines = (Path(config_dir) / "recent-files.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return lines[0] if lines else ""
