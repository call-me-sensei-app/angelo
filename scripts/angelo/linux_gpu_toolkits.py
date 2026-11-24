#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The GPU SDK versions in a Linux release build's toolkit folder, read from the SDKs themselves::

    python3 linux_gpu_toolkits.py versions <toolkits> --source <tree>

``<toolkits>`` is laid out as Blender's buildbot config expects (build_files/buildbot/config/blender_linux.cmake):
``cuda-<major>.<minor>`` per pinned CUDA toolkit, ``optix-sdk-<v>``, ``rocm-<v>`` and ``dlss-sdk-<v>``. Each version
comes from the SDK's own files: CUDA's ``version.json`` (or NVIDIA's release manifest beside a redistributable
assembly, ``linux_gpu_toolkits_fetch.py``), OptiX's ``OPTIX_VERSION`` in ``include/optix.h``, ROCm's
``.info/version``. DLSS's headers carry no product version, so its folder name is read, and the folder must hold
``include/nvsdk_ngx.h``. Prints ``{"cuda": [...], "optix": ..., "hip": ..., "dlss": ...}`` and exits 1 when a version
differs from Blender's pin (``gpu_sdk_pins.py``, the one judge). Standard library only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpu_sdk_pins  # noqa: E402


def cuda_version(folder):
    """An installed toolkit's ``version.json``, or the release manifest a redistributable assembly keeps
    (``linux_gpu_toolkits_fetch.py``); either way the folder must hold ``bin/nvcc``."""
    folder = Path(folder)
    if not (folder / "bin/nvcc").is_file():
        raise ValueError("%s lacks bin/nvcc" % folder)
    if (folder / "version.json").is_file():
        return json.loads((folder / "version.json").read_text(encoding="utf-8"))["cuda"]["version"]
    return json.loads((folder / "redistrib.json").read_text(encoding="utf-8"))["release_label"]


def optix_version(folder):
    text = (Path(folder) / "include/optix.h").read_text(encoding="utf-8", errors="replace")
    number = int(re.search(r"#define\s+OPTIX_VERSION\s+(\d+)", text).group(1))
    return "%d.%d.%d" % (number // 10000, number // 100 % 100, number % 100)


def hip_version(folder):
    text = (Path(folder) / ".info/version").read_text(encoding="utf-8").strip()
    return text.split("-", 1)[0]


def dlss_version(folder):
    folder = Path(folder)
    if not (folder / "include/nvsdk_ngx.h").is_file():
        raise ValueError("%s lacks include/nvsdk_ngx.h" % folder)
    return folder.name[len("dlss-sdk-"):]


def versions(toolkits):
    """What ``toolkits`` holds; a missing or unreadable SDK is left out, so the judge names it."""
    toolkits = Path(toolkits)
    found = {"cuda": []}
    readers = (("cuda-", "cuda", cuda_version), ("optix-sdk-", "optix", optix_version),
               ("rocm-", "hip", hip_version), ("dlss-sdk-", "dlss", dlss_version))
    for entry in sorted(toolkits.iterdir()):
        for prefix, name, reader in readers:
            if entry.is_dir() and entry.name.startswith(prefix):
                try:
                    version = reader(entry)
                except (OSError, ValueError, KeyError, AttributeError) as error:
                    print("gpu toolkits: %s: %s" % (entry.name, error), file=sys.stderr)
                    continue
                if name == "cuda":
                    found["cuda"].append(version)
                else:
                    found[name] = version
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    reading = commands.add_parser("versions")
    reading.add_argument("toolkits")
    reading.add_argument("--source", required=True)
    arguments = parser.parse_args(argv)
    used = versions(arguments.toolkits)
    pins = gpu_sdk_pins.source_pins(arguments.source, "linux")
    found, covered = gpu_sdk_pins.judge(pins, used, gpu_sdk_pins.source_exceptions(arguments.source))
    for line in found + covered:
        print("gpu toolkits: %s" % line, file=sys.stderr)
    print(json.dumps(used, indent=1, sort_keys=True))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
