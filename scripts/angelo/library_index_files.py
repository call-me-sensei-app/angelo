# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library index's command line: export ToonLab's catalog, hash, stage and (on request) upload.

    python library_index_files.py export-pro --out pro.json            # SUPABASE_URL + SUPABASE_ANON_KEY
    python library_index_files.py build --pro pro.json --oss oss.json --work <dir> --version 2026-10-30.1
    python library_index_files.py upload --work <dir> [--yes]          # R2_* variables; a dry run without --yes

``export-pro`` reads ToonLab Pro's ``cc0_assets`` through Supabase's REST API with the **public** anon key: the
table's policy is ``select using (true)``, so no service-role credential is needed, and it is 9 paged requests to
Supabase, never ToonLab's rate-limited ``/api/v1/assets``. The OSS export is a ``psql`` query over ToonLab's seed
releases (library.rst, "Building the index").

``build`` hashes every default file the catalog gives no SHA-256 for (streamed, never stored; a JSON cache keyed by
URL and size makes a rebuild incremental, as the objects are immutable) and writes ``index-<version>.json.gz`` plus
``latest.json`` (the version,
the index's URL, size and SHA-256: the one small mutable object the client polls).

``upload`` lists every object it would write under ``PREFIX`` and writes only with ``--yes``. The index is
immutable (``Cache-Control: immutable``); ``latest.json`` is cached for five minutes.

Standard library only.
"""

import argparse
import concurrent.futures
import gzip
import hashlib
import http.client
import json
import os
import ssl
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library_index as index  # noqa: E402

PUBLIC_BASE = "https://assets.toonlab.io"
PREFIX = "catalog/v1"   # ToonLab's catalog export, beside its official/ and mirrors/ prefixes.
COLUMNS = ("id,source,source_id,kind,name,categories,tags,license,attribution,thumb_path,styled_thumb_path,file_path,"
           "download_url,source_url,popularity,metadata,imported_at")
USER_AGENT = "Angelo-Library-Index/1 (+https://angelo.studio)"


def _tls():
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        try:
            import certifi
            context.load_verify_locations(certifi.where())
        except ImportError:
            pass
    return context


def export_pro(out):
    base, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_ANON_KEY"]
    rows, offset = [], 0
    while True:
        request = urllib.request.Request("%s/rest/v1/cc0_assets?select=%s&order=id&offset=%d&limit=1000"
                                         % (base, COLUMNS, offset),
                                         headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=120, context=_tls()) as response:
            page = json.load(response)
        rows += page
        offset += len(page)
        if len(page) < 1000:
            break
        time.sleep(0.5)
    Path(out).write_text(json.dumps(rows))
    print("exported %d rows" % len(rows))


def _stream(url, sink, limit):
    parts = urlsplit(url)
    connection = http.client.HTTPSConnection(parts.hostname, 443, timeout=60, context=_tls())
    try:
        connection.request("GET", parts.path + ("?" + parts.query if parts.query else ""),
                           headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("HTTP %d for %s" % (response.status, url))
        total = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                return total
            total += len(chunk)
            if total > limit:
                raise OSError("larger than %d bytes: %s" % (limit, url))
            sink(chunk)
    finally:
        connection.close()


def _digest(url, size):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    def sink(chunk):
        sha.update(chunk)
        md5.update(chunk)
    got = _stream(url, sink, size)
    if got != size:
        raise OSError("size %d, catalog says %d: %s" % (got, size, url))
    return sha.hexdigest(), md5.hexdigest()


def hash_missing(items, cache_path, workers=4):
    """Fill ``sha256`` on every default file without one; a file whose recorded MD5 disagrees fails the build."""
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    lock, done = threading.Lock(), [0]
    wanted = [f for f in index.needing_digest(items) if cache.get(f["url"], {}).get("size") != f["size"]]
    def work(entry):
        sha, md5 = _digest(entry["url"], entry["size"])
        with lock:
            cache[entry["url"]] = {"size": entry["size"], "sha256": sha, "md5": md5}
            done[0] += 1
            if done[0] % 250 == 0:
                cache_path.write_text(json.dumps(cache))
                print("hashed %d / %d" % (done[0], len(wanted)), flush=True)
    failures = []
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for entry, future in [(e, pool.submit(work, e)) for e in wanted]:
            try:
                future.result()
            except OSError as error:
                failures.append("%s: %s" % (entry["url"], error))
    cache_path.write_text(json.dumps(cache))
    for f in index.needing_digest(items):
        known = cache.get(f["url"])
        if known and known["size"] == f["size"]:
            if f.get("md5") and f["md5"] != known["md5"]:
                failures.append("MD5 mismatch: %s" % f["url"])
            f["sha256"] = known["sha256"]
    return failures



def build(args):
    work = Path(args.work)
    staging = work / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    items, report = index.build(json.loads(Path(args.pro).read_text()), json.loads(Path(args.oss).read_text()))
    failures = hash_missing(items, work / "hash-cache.json", args.workers)
    missing = index.needing_digest(items)
    if failures or missing:
        (work / "failures.txt").write_text("\n".join(failures))
        if missing:   # An item whose default files can't all be checked is left out, never listed unchecked.
            bad = {id(f) for f in missing}
            items[:] = [i for i in items if not any(id(f) in bad for f in i["files"])]
            report["skipped"]["default file could not be hashed"] = len(missing)
        report["listed"] = len(items)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = index.encode(index.document(items, args.version, generated, "%s/%s" % (PUBLIC_BASE, PREFIX)))
    packed = gzip.compress(raw, 9, mtime=0)
    name = "index-%s.json.gz" % args.version
    (staging / name).write_bytes(packed)
    latest = {"schema": index.SCHEMA, "version": args.version, "url": "%s/%s/%s" % (PUBLIC_BASE, PREFIX, name),
              "size": len(packed), "sha256": hashlib.sha256(packed).hexdigest(), "items": len(items)}
    (staging / "latest.json").write_text(json.dumps(latest, indent=1))
    report.update(index_bytes=len(packed), failures=len(failures))
    (work / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def upload(args):
    staging = Path(args.work) / "staging"
    plan = sorted(p for p in staging.rglob("*") if p.is_file())
    print("Objects to write under %s/%s (bucket %s):" % (PUBLIC_BASE, PREFIX, os.environ.get("R2_BUCKET", "?")))
    for path in plan:
        print("  %s  %d bytes" % (path.relative_to(staging).as_posix(), path.stat().st_size))
    if not args.yes:
        print("Dry run: nothing written. Re-run with --yes to upload.")
        return
    from r2_client import Bucket, Credentials
    bucket = Bucket("https://%s.r2.cloudflarestorage.com" % os.environ["R2_ACCOUNT_ID"], os.environ["R2_BUCKET"],
                    Credentials(os.environ["R2_ACCESS_KEY_ID"], os.environ["R2_SECRET_ACCESS_KEY"]))
    ordered = [p for p in plan if p.name != "latest.json"] + [p for p in plan if p.name == "latest.json"]
    for path in ordered:   # The pointer last: a client never sees a version whose index isn't there yet.
        key = "%s/%s" % (PREFIX, path.relative_to(staging).as_posix())
        data = path.read_bytes()
        kind = {".gz": "application/gzip", ".json": "application/json", ".webp": "image/webp", ".png": "image/png",
                ".jpg": "image/jpeg"}.get(path.suffix, "application/octet-stream")
        cache = "public, max-age=300" if path.name == "latest.json" else "public, max-age=31536000, immutable"
        if path.name != "latest.json" and bucket.head(key)[0] == 200:
            print("kept", key, "(immutable; already there)")
            continue
        status = bucket.put_bytes(key, data, {"Content-Type": kind, "Cache-Control": cache})
        if status != 200:
            raise SystemExit("R2 refused %s (HTTP %d); latest.json was not moved" % (key, status))
        print("wrote", key)


def verify(args):
    """Every staged object, read back from its public link: same size and SHA-256. Exit 1 on any difference."""
    staging = Path(args.work) / "staging"
    plan = sorted(p for p in staging.rglob("*") if p.is_file())
    bad = []
    def check(path):
        url = "%s/%s/%s" % (PUBLIC_BASE, PREFIX, path.relative_to(staging).as_posix())
        want, chunks = path.read_bytes(), []
        try:
            _stream(url, chunks.append, len(want) + 1)
        except OSError as error:
            return url, str(error)
        got = b"".join(chunks)
        if len(got) != len(want) or hashlib.sha256(got).digest() != hashlib.sha256(want).digest():
            return url, "differs (%d bytes, expected %d)" % (len(got), len(want))
        return None
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        bad = [r for r in pool.map(check, plan) if r]
    print("verified %d of %d objects" % (len(plan) - len(bad), len(plan)))
    for url, reason in bad[:20]:
        print("  %s: %s" % (url, reason))
    raise SystemExit(1 if bad else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-pro")
    export.add_argument("--out", required=True)
    make = commands.add_parser("build")
    make.add_argument("--pro", required=True)
    make.add_argument("--oss", required=True)
    make.add_argument("--work", required=True)
    make.add_argument("--version", required=True)
    make.add_argument("--workers", type=int, default=4)
    send = commands.add_parser("upload")
    send.add_argument("--work", required=True)
    send.add_argument("--yes", action="store_true")
    check = commands.add_parser("verify")
    check.add_argument("--work", required=True)
    args = parser.parse_args(argv)
    {"export-pro": lambda: export_pro(args.out), "build": lambda: build(args), "upload": lambda: upload(args),
     "verify": lambda: verify(args)}[args.command]()


if __name__ == "__main__":
    main()
