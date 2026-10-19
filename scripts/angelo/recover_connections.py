# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit legacy metadata recovery. No credential files or tokens are read."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons_core"))
from angelo_agent.connections import ConnectionStore
from angelo_agent.data_migration import migrate_legacy_data
from angelo_agent.storage import connection_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="sources", action="append", required=True, help="A previous instance's state directory")
    parser.add_argument("--destination", type=Path, default=None)
    arguments = parser.parse_args()
    if arguments.destination is None:
        # Writing the default store first would turn a later pre-rename data-folder move into a conflict.
        migration = migrate_legacy_data()
        if migration.needs_attention:
            parser.error(migration.message)
    store = ConnectionStore(arguments.destination or connection_directory())
    recovered = []
    for source in arguments.sources:
        source = Path(source).absolute()
        if not (source / "connections.json").is_file():
            parser.error("The specified source has no connections.json metadata")
        recovered.extend(store.import_metadata(source))
    print(json.dumps({"directory": str(store.directory), "recovered": len(recovered),
                      "connections": [{"id": r["id"], "name": r["name"], "mode": r["mode"]} for r in store.records.values()],
                      "credential_files_read_or_copied": False}, indent=2))


if __name__ == "__main__":
    main()
