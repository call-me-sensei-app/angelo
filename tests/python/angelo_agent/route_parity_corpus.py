#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the route-parity corpus (``route_parity_corpus/``) from recorded chats (scripts/angelo/route_parity.py).

A recorded chat (``<state>/sessions/<id>.json``) keeps every tool result as the executor returned it, so its results
are what each route's presentation code really receives. For each (tool, status, certified) this keeps up to three
results that together hold the most distinct field locations, plus results holding a field named in ``--want``;
each list keeps its three rows holding the most fields and any other row holding a field they lack, and each string
its first ``MAX_STRING`` characters. Paths, user and host names are replaced, and the build refuses to write anything
that still looks like one.

    python3 tests/python/angelo_agent/route_parity_corpus.py <state or sessions folder>... --out <folder>
        [--want motion,world] [--label "recorded trials, 1.0.3 candidate"]

``certified``: a host summary row follows the result (``routing._certified_rows``), as when the host's checks
proved the whole plan. The native suite's ``test_blender_route_parity`` writes fresh results the same way.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/angelo"))
import builder_paths  # noqa: E402

MAX_ROWS, MAX_STRING, PER_KIND = 3, 160, 3
PATHS = re.compile(r"(?:\b[A-Za-z]:[\\/]|/Users/|/home/|/private/|/var/folders/|/tmp/|/Volumes/)[^\"\s,;)]*")
# This machine's user and host names (builder_paths.builder_identity), the publishing surname and any address.
PERSONAL = re.compile("|".join([re.escape(name) for name in builder_paths.builder_identity()]
                               + [r"vinijtrongjit", r"@[a-z0-9-]+\.[a-z]{2,}"]), re.IGNORECASE)


def field_paths(value, path="", found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(path + "." + key)
            field_paths(child, path + "." + key, found)
    elif isinstance(value, list):
        for child in value[:64]:
            field_paths(child, path + "[]", found)
    return found


def shrink(value):
    """``value`` with each list cut to the ``MAX_ROWS`` rows holding the most fields, plus any other row holding a field
    they lack (so no field location is lost: the registry may only name what the corpus holds), strings cut, paths
    replaced."""
    if isinstance(value, dict):
        return {PATHS.sub("<path>", key): shrink(child) for key, child in value.items()}
    if isinstance(value, list):
        if len(value) <= MAX_ROWS:
            return [shrink(child) for child in value]
        chosen, covered = [], set()
        while len(chosen) < len(value):
            index = max((i for i in range(len(value)) if i not in chosen),
                        key=lambda i: len(field_paths(value[i]) - covered))
            if len(chosen) >= MAX_ROWS and not field_paths(value[index]) - covered:
                break
            chosen.append(index)
            covered |= field_paths(value[index])
        return [shrink(value[index]) for index in sorted(chosen)]
    if isinstance(value, str):
        value = PATHS.sub("<path>", value)
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…"
    return value


def strings(value):
    """Every key and string in ``value``."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif isinstance(value, str):
        yield value


def recorded(folders):
    """``(tool, certified, result)`` of every completed or failed activity row in the recorded chats."""
    for folder in folders:
        folder = Path(folder)
        files = [folder] if folder.is_file() else sorted(folder.rglob("sessions/*.json")) or sorted(folder.glob("*.json"))
        for path in files:
            try:
                messages = json.loads(path.read_text()).get("messages", [])
            except (OSError, ValueError, AttributeError):
                continue
            for index, message in enumerate(messages):
                result = message.get("result")
                if message.get("role") != "activity" or not isinstance(result, dict) or not message.get("tool"):
                    continue
                if message.get("status") not in ("completed", "failed") or message["tool"] == "StructuredOutput":
                    continue
                following = next((row for row in messages[index + 1:] if row.get("role") not in ("activity", "thinking")
                                  and row.get("notice") is not True), {})
                yield message["tool"], following.get("role") == "system" and following.get("host_summary") is True, result


def kind(tool, result):
    status = result.get("status") if isinstance(result.get("status"), str) else (
        "error" if result.get("error") else "ok" if result.get("success") else "plain")
    return tool, status


def pick(rows, want=()):
    """Up to ``PER_KIND`` results per (tool, status, certified) covering the most field locations, plus up to two
    results per wanted field."""
    groups, chosen, wanted = defaultdict(list), [], defaultdict(int)
    for tool, certified, result in rows:
        groups[kind(tool, result) + (certified,)].append((tool, certified, result, field_paths(result)))
        for field in want:
            if field in result and wanted[field] < 2:
                wanted[field] += 1
                chosen.append((tool, certified, result))
    for _group, members in sorted(groups.items()):
        covered = set()
        for _ in range(PER_KIND):
            tool, certified, result, paths = max(members, key=lambda member: len(member[3] - covered))
            if len(paths - covered) < (1 if not covered else 3):
                break
            covered |= paths
            chosen.append((tool, certified, result))
    return chosen


def build(folders, out, want=(), label="recorded trials"):
    items = [{"tool": tool, "certified": certified, "source": label, "result": shrink(result)}
             for tool, certified, result in pick(recorded(folders), want)]
    for text in strings(items):
        leaked = PATHS.search(text) or PERSONAL.search(text)
        if leaked:
            raise SystemExit("refusing to write: %r looks like a path or a personal name" % leaked.group(0))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    by_tool = defaultdict(list)
    for item in items:
        by_tool[item["tool"]].append(item)
    for tool, members in by_tool.items():
        (out / ("%s.json" % tool)).write_text(json.dumps(members, ensure_ascii=False, indent=1) + "\n")
    return items


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folders", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--want", default="")
    parser.add_argument("--label", default="recorded trials")
    args = parser.parse_args(argv)
    items = build(args.folders, args.out, [field for field in args.want.split(",") if field], args.label)
    print("%d results in %s" % (len(items), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
