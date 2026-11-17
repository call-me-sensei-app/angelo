# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build, render and check the renamed-and-removed table (host Python).

    # 1. One dump per Blender release, each inside that release (official builds, background):
    blender-4.2 --background --factory-startup --python scripts/angelo/api_drift/dump.py -- --out dumps/4.2.json
    # 2. The table, from every dump (oldest to newest; the newest is the target) and this checkout's source:
    python3 scripts/angelo/api_drift/cli.py build dumps/*.json
    # 3. After editing curated.json only (keeps the dumped rows of the committed table):
    python3 scripts/angelo/api_drift/cli.py build --keep-dumped
    # The C++ rows must match the table (the host suite runs this):
    python3 scripts/angelo/api_drift/cli.py check

``build`` writes ``TABLE`` and renders ``CPP_TABLE`` from it. The dumps are inputs, kept outside the repository
(several megabytes each); the table records which releases it was built from.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

from api_drift import table, versioning  # noqa: E402

TABLE = ROOT / "scripts/addons_core/angelo_agent/assets/api_drift.json"
CPP_TABLE = ROOT / "source/blender/python/intern/bpy_angelo_renamed_api_table.inc"
CURATED = HERE / "curated.json"


def write_table(built):
    TABLE.write_text(json.dumps(built, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    CPP_TABLE.write_text(table.render_cpp(built), encoding="utf-8")


def build(args):
    curated = json.loads(CURATED.read_text(encoding="utf-8"))
    renames = versioning.socket_renames(ROOT)
    if args.keep_dumped:
        built = rebuild_without_dumps(curated, renames)
    else:
        built = table.build(table.load_dumps(args.dumps), renames, curated)
    write_table(built)
    print(json.dumps({"entries": len(built["entries"]), "dumped": built["dumped"], "table": str(TABLE.relative_to(ROOT)),
                      "cpp": str(CPP_TABLE.relative_to(ROOT))}))
    return 0


def rebuild_without_dumps(curated, renames):
    """The committed table's dumped rows, with the curated rows applied again.

    The versioning rows were checked against the target's dump when the table was built from dumps, so they
    are kept as they are; a table never built from dumps takes them from the source again, unchecked (the
    native suite then checks each against the running build).
    """
    previous = json.loads(TABLE.read_text(encoding="utf-8")) if TABLE.exists() else {"entries": [], "dumped": []}
    found = {}
    for entry in previous["entries"]:
        if entry.get("evidence") == "curated" or (entry.get("evidence") == "versioning" and not previous["dumped"]):
            continue
        entry = dict(entry, dumped=previous["dumped"])
        entry.pop("hint", None)
        entry.pop("certain", None)
        found[(entry["use"], entry.get("owner"), entry.get("property"), entry["name"])] = entry
    if not previous["dumped"]:
        found = table.apply_versioning(found, renames, None)
    found = table.merge_curated(found, curated)
    entries = []
    for key in sorted(found, key=lambda key: tuple("" if part is None else str(part) for part in key)):
        entry = found[key]
        entry.setdefault("replacements", [])
        entry["hint"] = table.hint(entry)
        entry.pop("dumped", None)
        entries.append({field: entry[field] for field in sorted(entry) if entry[field] is not None})
    return {"schema": table.TABLE_SCHEMA, "target": curated["target"], "dumped": previous["dumped"],
            "entries": entries}


def check(_args):
    built = json.loads(TABLE.read_text(encoding="utf-8"))
    expected = table.render_cpp(built)
    actual = CPP_TABLE.read_text(encoding="utf-8") if CPP_TABLE.exists() else ""
    if expected != actual:
        print("%s does not match %s: run cli.py build --keep-dumped" % (CPP_TABLE.name, TABLE.name))
        return 1
    print("ok: %d entries" % len(built["entries"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="build the table from dumps (or keep the dumped rows)")
    build_parser.add_argument("dumps", nargs="*", help="dump.py outputs, any order; the newest is the target")
    build_parser.add_argument("--keep-dumped", action="store_true",
                              help="no dumps: keep the committed table's dumped rows, apply curated.json again")
    build_parser.set_defaults(run=build)
    commands.add_parser("check", help="the C++ rows match the table").set_defaults(run=check)
    args = parser.parse_args(argv)
    if args.command == "build" and not args.dumps and not args.keep_dumped:
        parser.error("give the dumps, or --keep-dumped")
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
