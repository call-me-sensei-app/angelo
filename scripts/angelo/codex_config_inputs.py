#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare a Codex fork commit with tests/python/angelo_agent/codex_config_keys.py (the recorded lists: this script's
own name differs, so the two are never one module in a process).

Usage: codex_config_inputs.py --fork <checkout> [--commit <sha>]. Reads, at the commit (default: the pin's fork.commit):
the top-level keys and the ``[features]`` keys of ``codex-rs/core/config.schema.json``, and the person's config
settings Codex's sign-in configuration reads (``derive``): every ``config.<key>`` and ``Feature::<Name>`` (named by its
FeatureSpec key) in the two builders of the auth configuration, ``bootstrap_auth_config`` and ``Config::auth_config``,
and in every function of the scanned sources they call, transitively. Each input is printed with the other files of
the fork that read it: one also read outside the sign-in needs a ruling before it is passed on (re-read of
0b6b952594: the proxy features route every request). Prints what was added or removed against the recorded lists and
the values to write; classify each addition by hand (doc/angelo/releasing.rst, "Agent runtime"). Exit 1 when anything
differs; exit 2 (``DerivationError``) when the fork's auth code no longer has the shape this reads, never a smaller set.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "codex-rs/core/config.schema.json"
SOURCES = ("codex-rs/core/src/config/auth_keyring.rs", "codex-rs/core/src/config/mod.rs")
FEATURES = "codex-rs/features/src/lib.rs"
ROOTS = ("bootstrap_auth_config", "auth_config")   # The auth configuration's bootstrap form, and Config's own.
CONFIG_READ = re.compile(r"\b(?:config|cfg|config_toml)\s*\.\s*(\w+)")
SPEC = re.compile(r"id:\s*Feature::(\w+),\s*key:\s*\"(\w+)\"")
# Reads that are not config keys: the feature table is read through Feature:: names.
NOT_KEYS = {"features", "experimental_use_unified_exec_tool"}


class DerivationError(Exception):
    """The fork's auth code changed shape: fix this reader rather than trust a smaller set."""


def functions(source):
    """``{name: body}`` of every Rust ``fn`` in ``source`` (brace-matched; same-named functions joined)."""
    found = {}
    for match in re.finditer(r"\bfn\s+(\w+)\b[^{;]*\{", source):
        depth, index = 1, match.end()
        while depth and index < len(source):
            depth += {"{": 1, "}": -1}.get(source[index], 0)
            index += 1
        found[match.group(1)] = found.get(match.group(1), "") + source[match.end():index]
    return found


def derive(sources, features_source):
    """``(inputs, visited)``: the person's config settings the auth configuration reads, and the functions read.

    Raises ``DerivationError`` when a root is missing or empty, when no FeatureSpec parses, or when a
    ``Feature::<Name>`` the bodies use has no spec."""
    defined = functions(sources)
    missing = [name for name in ROOTS if not defined.get(name, "").strip()]
    if missing:
        raise DerivationError("the fork no longer defines %s (or its body is empty): read the new auth configuration"
                              % ", ".join(missing))
    specs = dict(SPEC.findall(features_source))
    if not specs:
        raise DerivationError("no FeatureSpec { id: Feature::..., key: \"...\" } parsed in %s" % FEATURES)
    visited, queue = set(), list(ROOTS)
    while queue:
        name = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        queue += [called for called in re.findall(r"\b(\w+)\s*\(", defined[name]) if called in defined]
    bodies = "".join(defined[name] for name in sorted(visited))
    keys = set(CONFIG_READ.findall(bodies)) - NOT_KEYS
    names = set(re.findall(r"\bFeature::(\w+)", bodies))
    unknown = sorted(names - set(specs))
    if unknown:
        raise DerivationError("Feature::%s has no FeatureSpec key: read its spec" % ", Feature::".join(unknown))
    return tuple(sorted(keys | {"features." + specs[name] for name in names})), tuple(sorted(visited))


def _git(fork, *args):
    return subprocess.run(["git", "-C", str(fork), *args], check=True, capture_output=True, text=True).stdout


def auth_inputs(fork, commit):
    """``derive`` on the fork at ``commit``."""
    return derive("\n".join(_git(fork, "show", "%s:%s" % (commit, path)) for path in SOURCES),
                  _git(fork, "show", "%s:%s" % (commit, FEATURES)))


def also_read_by(fork, commit, setting, visited=()):
    """Where else the fork reads ``setting`` (its Feature:: name or ``.<key>``): the scanned sources' functions outside
    the sign-in's (``visited``), named ``fn:<name>``, and every other non-test source file."""
    if setting.startswith("features."):
        specs = {key: name for name, key in SPEC.findall(_git(fork, "show", "%s:%s" % (commit, FEATURES)))}
        word = "Feature::" + specs[setting.split(".", 1)[1]]
    else:
        word = "." + setting
    try:
        listed = _git(fork, "grep", "-l", "-F", "-w", "-e", word, commit, "--", "codex-rs/*.rs")
    except subprocess.CalledProcessError:
        return []
    files = [line.split(":", 1)[1] for line in listed.splitlines()]
    scanned = functions("\n".join(_git(fork, "show", "%s:%s" % (commit, path)) for path in SOURCES))
    inside = ["fn:" + name for name, body in sorted(scanned.items())
              if name not in visited and re.search(r"(?<!\w)%s(?!\w)" % re.escape(word), body)]
    return inside + [name for name in files if name not in SOURCES and "test" not in name and name != FEATURES]


def main(argv=None):
    sys.path.insert(0, str(ROOT / "tests/python/angelo_agent"))
    import codex_config_keys
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fork", required=True, type=Path)
    parser.add_argument("--commit")
    arguments = parser.parse_args(argv)
    pin = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))
    commit = arguments.commit or pin["fork"]["commit"]
    schema = json.loads(_git(arguments.fork, "show", "%s:%s" % (commit, SCHEMA)))
    try:
        inputs, visited = auth_inputs(arguments.fork, commit)
    except DerivationError as error:
        print("cannot derive the sign-in inputs: %s" % error, file=sys.stderr)
        return 2
    found = {"top-level key": set(schema["properties"]),
             "features key": set(schema["properties"]["features"].get("properties", {})),
             "sign-in input": set(inputs)}
    known = {"top-level key": set(codex_config_keys.KEYS), "features key": set(codex_config_keys.FEATURES),
             "sign-in input": set(codex_config_keys.AUTH_INPUTS)}
    differs = commit != codex_config_keys.SCHEMA_FORK_COMMIT
    for kind in found:
        for key in sorted(found[kind] - known[kind]):
            print("added   %s %s" % (kind, key))
        for key in sorted(known[kind] - found[kind]):
            print("removed %s %s" % (kind, key))
        differs |= found[kind] != known[kind]
    print("read from: %s" % ", ".join(visited))
    for setting in inputs:
        others = also_read_by(arguments.fork, commit, setting, visited)
        listed = (": " + ", ".join(others[:4])) if others else ""
        print("%-40s also read in %d place(s)%s" % (setting, len(others), listed))
    print('SCHEMA_FORK_COMMIT = "%s"' % commit)
    print("AUTH_INPUTS = %r" % (inputs,))
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
