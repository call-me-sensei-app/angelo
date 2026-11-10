# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Download the game-physics e2e's held-out models (``heldout_assets.json``) and check each one's sha256.

    python3 -I fetch_heldout_assets.py DEST            # DEST becomes ANGELO_QA_PHYSICS_ASSETS
    python3 -I fetch_heldout_assets.py DEST --record   # list the sources again by the rule and rewrite the manifest

The models are third-party CC0 files and are never committed (they would ship in the installer). Every file comes
from a source pinned to a commit, so the bytes never change; a hash mismatch or a file that isn't a GLB stops the
run. ``--record`` applies the manifest's rule to each source's tree at its commit (GitHub's public API) and
records every matching file, its licence file's hash and each file's sha256. Standard library only; the downloads
are data and nothing in them is run.
"""

import argparse
import fnmatch
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "heldout_assets.json"
RAW = "https://raw.githubusercontent.com/%s/%s/%s"
TREE = "https://api.github.com/repos/%s/git/trees/%s?recursive=1"


def raw_url(repo, commit, path):
    return RAW % (repo, commit, urllib.parse.quote(path))


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "angelo-heldout-fetch"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def checked(data, name):
    """The bytes, if they are a GLB (not an LFS pointer or an error page)."""
    if data[:4] != b"glTF":
        raise SystemExit("%s is not a GLB (starts %r)" % (name, data[:24]))
    return data


def record(manifest, dest):
    dest.mkdir(parents=True, exist_ok=True)
    models, licences = [], {}
    for source in manifest["sources"]:
        tree = json.loads(get(TREE % (source["repo"], source["commit"])))
        if tree.get("truncated"):
            raise SystemExit("%s: the tree listing is truncated" % source["repo"])
        folder = source["glob"].rsplit("/", 1)[0] + "/"
        paths = sorted(item["path"] for item in tree["tree"] if item["type"] == "blob" and item["path"].startswith(
            folder) and "/" not in item["path"][len(folder):] and fnmatch.fnmatchcase(item["path"], source["glob"]))
        licence = get(raw_url(source["repo"], source["commit"], source["licence_file"]))
        if source["licence_statement"].encode() not in licence:
            raise SystemExit("%s: %s no longer says %r" % (source["kit"], source["licence_file"],
                                                           source["licence_statement"]))
        licences[source["kit"]] = hashlib.sha256(licence).hexdigest()
        for path in paths:
            url, name = raw_url(source["repo"], source["commit"], path), source["prefix"] + path.rsplit("/", 1)[-1]
            data = checked(get(url), path)
            (dest / name).write_bytes(data)
            models.append({"file": name, "kit": source["kit"], "path": path,
                           "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    names = [item["file"] for item in models]
    if len(set(names)) != len(names):
        raise SystemExit("local names collide")
    for source in manifest["sources"]:
        source["licence_url"] = raw_url(source["repo"], source["commit"], source["licence_file"])
        source["licence_sha256"] = licences[source["kit"]]
        source["models"] = sum(1 for item in models if item["kit"] == source["kit"])
    manifest["models"] = models
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return models


def fetch(manifest, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for item in manifest["models"]:
        target = dest / item["file"]
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
            continue
        data = checked(get(item["url"]), item["file"])
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise SystemExit("%s: sha256 mismatch" % item["file"])
        target.write_bytes(data)
    return manifest["models"]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("dest")
    parser.add_argument("--record", action="store_true", help="re-list the sources by the rule, rewrite the manifest")
    args = parser.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.record:
        record(manifest, Path(args.dest))
    models = fetch(manifest, Path(args.dest))
    print("%d held-out models in %s" % (len(models), args.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
