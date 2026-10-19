#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The third-party notices of Angelo's agent runtime: every crate and V8 component its binaries contain.

The agent runtime is the patched OpenAI Codex build Angelo bundles (``agent_runtime.json``): ``codex`` and
``codex-code-mode-host`` on macOS, plus the sandbox helpers on Windows. Upstream publishes no notices for
them, so this tool writes ``release/license/agent-runtime/THIRD-PARTY-NOTICES.txt`` from the fork's own
lock file, at the pinned commit::

    python3 scripts/angelo/agent_runtime_notices.py --fork ../angelo-codex-runtime [--cargo cargo]

* The fork is only read: ``git archive`` of the pinned commit's ``codex-rs`` goes to a temporary folder
  (without its ``rust-toolchain.toml``, so rustup downloads no toolchain), and ``cargo metadata --offline``
  runs there once per platform target in the pin. Every crate it resolves must be the version the fork's
  ``Cargo.lock`` pins, or the tool stops.
* The crates are the normal (run-time) dependencies of the packages that build the pinned binaries, for
  their platform targets; build scripts, procedural macros and development dependencies do not ship and are
  left out. Each crate's own license files are reproduced once per distinct text; a crate that ships none
  names its SPDX expression, whose texts are in ``release/license/spdx`` (checked), and its authors.
* The ``v8`` crate links a prebuilt V8 into ``codex-code-mode-host``. Its components (``V8_COMPONENTS``) are
  listed by hand, each checked against the crate's source tree, and the notices their licenses require
  verbatim are copied from that source.

Needs the fork checkout, Cargo and the fork's crates already in Cargo's registry cache (a build of the
runtime puts them there). Network: none. Standard library only.
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json"
LICENSES = ROOT / "release/license"
OUT = LICENSES / "agent-runtime" / "THIRD-PARTY-NOTICES.txt"
LICENSE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|notice|copyright|unlicense)")
HOST = "codex-code-mode-host"

# What the prebuilt V8 in the ``v8`` crate (rusty_v8) contains, as its source tree shows: (name, SPDX
# expression, attribution, (file in the crate, text that must be in it), (start, end) of a notice to copy
# verbatim from that file or None). Review on every ``v8`` crate update; the tool refuses a missing file
# or marker.
V8_COMPONENTS = (
    ("V8", "BSD-3-Clause", "Copyright the V8 project authors. All rights reserved.",
     ("v8/include/v8.h", "the V8 project authors"), None),
    ("ICU", "Unicode-3.0", "Copyright (C) 2016 and later: Unicode, Inc. and others.",
     ("third_party/icu/source/common/unicode/utypes.h", "Unicode, Inc. and others"), None),
    ("Abseil", "Apache-2.0", "Copyright The Abseil Authors.",
     ("third_party/abseil-cpp/absl/base/config.h", "The Abseil Authors"), None),
    ("Highway", "Apache-2.0 OR BSD-3-Clause", "Copyright Google LLC",
     ("third_party/highway/src/hwy/base.h", "Google LLC"), None),
    ("simdutf", "Apache-2.0 OR MIT", "Copyright the simdutf authors",
     ("third_party/simdutf/simdutf.h", "simdutf"),
     ("Copyright (c) 2016-     Facebook, Inc", "*/")),
    ("fast_float", "Apache-2.0 OR BSL-1.0 OR MIT", "Copyright (c) 2021 The fast_float authors",
     ("third_party/fast_float/src/include/fast_float/fast_float.h", "fast_float"), None),
    ("Dragonbox", "Apache-2.0 WITH LLVM-exception OR BSL-1.0", "Copyright 2020-2025 Junekey Jeon",
     ("third_party/dragonbox/src/include/dragonbox/dragonbox.h", "Junekey Jeon"), None),
    ("FP16", "MIT", "Copyright the FP16 authors (Marat Dukhan)",
     ("third_party/fp16/src/include/fp16/fp16.h", "fp16"), None),
    ("libc++, libc++abi, libunwind and LLVM libc", "Apache-2.0 WITH LLVM-exception",
     "Copyright the LLVM Project contributors",
     ("third_party/libc++/src/include/__config", "LLVM-exception"), None),
    ("PartitionAlloc", "BSD-3-Clause", "Copyright The Chromium Authors",
     ("third_party/partition_alloc/src/partition_alloc/partition_alloc.h", "The Chromium Authors"), None),
    ("Chromium DevTools protocol support (crdtp)", "BSD-3-Clause", "Copyright The Chromium Authors",
     ("v8/third_party/inspector_protocol/crdtp/json.h", "The Chromium Authors"), None),
    ("rapidhash", "BSD-2-Clause", "Copyright (C) 2024 Nicolas De Carli",
     ("v8/third_party/rapidhash-v8/rapidhash.h", "Nicolas De Carli"),
     ("rapidhash - ", "*/")),
    ("HalfSipHash", "CC0-1.0", "Jean-Philippe Aumasson (public domain dedication)",
     ("v8/third_party/siphash/halfsiphash.h", "Jean-Philippe Aumasson"), None),
    ("UTF-8 decoder", "MIT", "Copyright (c) 2008-2009 Bjoern Hoehrmann",
     ("v8/third_party/utf8-decoder/utf8-decoder.h", "hoehrmann"), None),
    ("fdlibm", "Sun Microsystems notice", "Copyright (C) 1993 by Sun Microsystems, Inc. All rights reserved.",
     ("v8/src/base/ieee754.cc", "Sun Microsystems"),
     ("// Copyright (C) 1993 by Sun Microsystems", "is preserved.")),
)


class NoticeError(Exception):
    pass


def pin():
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))


def extract(fork, commit, folder):
    """The pinned commit's ``codex-rs`` in ``folder`` (read from the fork's Git objects; the fork is unchanged)."""
    archive = subprocess.run(["git", "-C", str(fork), "archive", "--format=tar", commit, "codex-rs"],
                             capture_output=True, env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
    if archive.returncode != 0:
        raise NoticeError("git archive %s failed: %s" % (commit, archive.stderr.decode(errors="replace").strip()))
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(folder, filter="data")
    workspace = Path(folder, "codex-rs")
    (workspace / "rust-toolchain.toml").unlink(missing_ok=True)  # The default toolchain: nothing downloaded.
    return workspace


def metadata(workspace, cargo, target):
    env = dict(os.environ, CARGO_NET_OFFLINE="true")
    rustc = Path(cargo).parent / "rustc"
    if rustc.is_file() and "RUSTC" not in env:  # A toolchain's own cargo, called without rustup's proxy.
        env["RUSTC"] = str(rustc)
    done = subprocess.run([cargo, "metadata", "--offline", "--format-version", "1", "--filter-platform", target,
                           "--manifest-path", str(workspace / "Cargo.toml")], capture_output=True, text=True, env=env)
    if done.returncode != 0:
        raise NoticeError("cargo metadata for %s failed (are the fork's crates in Cargo's cache?): %s"
                          % (target, done.stderr.strip()[-800:]))
    return json.loads(done.stdout)


def runtime_closure(meta, binaries):
    """The third-party packages linked into ``binaries``: normal dependencies, never procedural macros."""
    packages = {package["id"]: package for package in meta["packages"]}
    nodes = {node["id"]: node for node in meta["resolve"]["nodes"]}
    roots = [package["id"] for package in meta["packages"] if package["source"] is None
             and any("bin" in target["kind"] and target["name"] in binaries for target in package["targets"])]
    found = {target["name"] for package in roots for target in packages[package]["targets"]}
    if not set(binaries) <= found:
        raise NoticeError("no package builds %s" % sorted(set(binaries) - found))
    seen, stack = set(), list(roots)
    while stack:
        identity = stack.pop()
        if identity in seen:
            continue
        seen.add(identity)
        for dependency in nodes[identity]["deps"]:
            child = packages[dependency["pkg"]]
            if any(kind["kind"] is None for kind in dependency["dep_kinds"]) and not any(
                    "proc-macro" in target["kind"] for target in child["targets"]):
                stack.append(dependency["pkg"])
    return {identity: packages[identity] for identity in seen if packages[identity]["source"]}


def license_files(package):
    folder = Path(package["manifest_path"]).parent
    return sorted(path for path in folder.iterdir() if path.is_file() and LICENSE_FILE.match(path.name))


def spdx_ids(expression):
    """The license identifiers of an SPDX expression (also the old ``MIT/Apache-2.0`` form)."""
    return sorted({word for word in re.split(r"[\s()/]+", expression or "") if word and word not in ("OR", "AND", "WITH")})


def spdx_text_exists(identifier):
    return (LICENSES / "spdx" / (identifier + ".txt")).is_file() or (LICENSES / "others" / (identifier + ".txt")).is_file()


def excerpt(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin) + len(end)].rstrip()


def v8_section(v8_folder):
    lines = ["The v8 crate (%s) links a prebuilt V8 into %s. V8 and the libraries built into it:" % (
        v8_folder.name, HOST), ""]
    notices = []
    for name, expression, attribution, (relative, marker), notice in V8_COMPONENTS:
        path = v8_folder / relative
        if not path.is_file():
            raise NoticeError("V8 component %s: %s is missing from the v8 crate; review V8_COMPONENTS" % (name, relative))
        text = path.read_text(encoding="utf-8", errors="replace")
        if marker.lower() not in text.lower():
            raise NoticeError("V8 component %s: %s no longer mentions %r; review V8_COMPONENTS" % (name, relative, marker))
        lines.append("* %s: %s. %s" % (name, expression, attribution))
        if notice:
            notices.append("%s (from %s):\n\n%s" % (name, relative, excerpt(text, *notice)))
    # Every license named here has its text in release/license, except the Unicode License V3 (reproduced with
    # the icu crates below), CC0 (a public domain dedication) and fdlibm's notice (reproduced below).
    expressions = " ".join(component[1] for component in V8_COMPONENTS if component[0] != "fdlibm")
    for identifier in spdx_ids(expressions):
        if identifier not in ("CC0-1.0", "Unicode-3.0") and not spdx_text_exists(identifier):
            raise NoticeError("no text for %s in release/license" % identifier)
    lines += ["", "Their license texts are in ../spdx and ../others, beside this folder; the Unicode License V3 "
              "(ICU) is reproduced with the icu crates below. HalfSipHash is dedicated to the public domain "
              "(CC0-1.0). These notices are reproduced as their sources state them:", ""]
    for notice in notices:
        lines += [notice, ""]
    return lines


def build(fork, cargo):
    runtime = pin()
    with tempfile.TemporaryDirectory(prefix="angelo-runtime-notices-") as folder:
        workspace = extract(fork, runtime["fork"]["commit"], folder)
        locked = {(package["name"], package["version"]) for package in
                  tomllib.loads((workspace / "Cargo.lock").read_text(encoding="utf-8"))["package"]
                  if "source" in package}
        crates, v8_folder = {}, None
        for platform, files in sorted(runtime["platforms"].items()):
            meta = metadata(workspace, cargo, files["rust_target"])
            for binary in [name[:-4] if name.endswith(".exe") else name for name in files["required"] + files["optional"]]:
                for package in runtime_closure(meta, [binary]).values():
                    if (package["name"], package["version"]) not in locked:
                        raise NoticeError("%s %s is not the version Cargo.lock pins" % (package["name"],
                                                                                       package["version"]))
                    entry = crates.setdefault((package["name"], package["version"]),
                                              {"package": package, "platforms": set(), "binaries": set()})
                    entry["platforms"].add(platform)
                    entry["binaries"].add(binary)
                    if package["name"] == "v8":
                        v8_folder = Path(package["manifest_path"]).parent
        if v8_folder is None:
            raise NoticeError("the v8 crate is not among the runtime's dependencies; review V8_COMPONENTS")
        texts, crate_lines, missing = {}, [], []
        for (name, version), entry in sorted(crates.items()):
            package, references = entry["package"], []
            for path in license_files(package):
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                key = " ".join(text.split())
                record = texts.setdefault(key, {"number": len(texts) + 1, "text": text, "users": []})
                record["users"].append("%s %s (%s)" % (name, version, path.name))
                references.append("[%d]" % record["number"])
            line = "* %s %s: %s; %s; in %s" % (name, version, package.get("license") or "no license field",
                                               ", ".join(sorted(entry["platforms"])), ", ".join(sorted(entry["binaries"])))
            if package.get("repository"):
                line += "; " + package["repository"]
            if references:
                line += "; text " + ", ".join(references)
            else:
                ids = spdx_ids(package.get("license"))
                missing += [identifier for identifier in ids if not spdx_text_exists(identifier)]
                line += "; texts in ../spdx (%s); authors: %s" % (", ".join(ids), "; ".join(package.get("authors") or
                                                                                         ["not stated"]))
            crate_lines.append(line)
        if missing:
            raise NoticeError("crates without license files use %s, which release/license lacks" % sorted(set(missing)))
    header = [
        "Third-party notices of Angelo's agent runtime",
        "=" * 45, "",
        "Angelo bundles a modified build of OpenAI Codex %s (README.txt beside this file): %s on macOS and %s on "
        "Windows. Besides OpenAI's own code (LICENSE.txt and NOTICE.txt beside this file), those programs contain the "
        "following third-party software, under the terms reproduced or named below." % (
            runtime["version"], ", ".join(runtime["platforms"]["macos"]["required"]),
            ", ".join(runtime["platforms"]["windows"]["required"] + runtime["platforms"]["windows"]["optional"])),
        "",
        "Written by scripts/angelo/agent_runtime_notices.py from the fork's Cargo.lock at %s: the run-time "
        "dependencies of those programs for %s." % (
            runtime["fork"]["commit"], " and ".join(files["rust_target"] for _platform, files in
                                                    sorted(runtime["platforms"].items()))),
        "", ""]
    body = (["Part 1. Components of V8", ""] + v8_section(v8_folder) + [""]
            + ["Part 2. Rust crates (%d)" % len(crate_lines), ""] + crate_lines + ["", "",
               "Part 3. License texts of the crates (%d distinct)" % len(texts), ""])
    for record in sorted(texts.values(), key=lambda record: record["number"]):
        body += ["-" * 78, "[%d] used by: %s" % (record["number"], ", ".join(record["users"])), "-" * 78, "",
                 record["text"], "", ""]
    return "\n".join(header + body).rstrip() + "\n", {"crates": len(crate_lines), "texts": len(texts)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fork", required=True, help="the Codex fork's checkout (read only)")
    parser.add_argument("--cargo", default=shutil.which("cargo") or "cargo")
    parser.add_argument("--out", default=str(OUT))
    arguments = parser.parse_args(argv)
    try:
        text, counts = build(Path(arguments.fork), arguments.cargo)
    except (NoticeError, OSError, ValueError, KeyError) as error:
        print("agent_runtime_notices: %s" % error, file=sys.stderr)
        return 1
    out = Path(arguments.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("%s: %d crates, %d distinct license texts, %d bytes" % (out, counts["crates"], counts["texts"], len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
