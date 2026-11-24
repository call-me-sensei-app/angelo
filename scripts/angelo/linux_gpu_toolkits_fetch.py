#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fetch the CUDA toolkits a Linux release build compiles its Cycles kernels with, at Blender's pins::

    python3 linux_gpu_toolkits_fetch.py cuda <toolkits> --source <tree>

For each CUDA version Blender pins (``gpu_sdk_pins.py``, ``build_files/buildbot/config/blender_version.cmake``), the
compiler's parts are downloaded from NVIDIA's redistributable archives and merged into ``<toolkits>/cuda-<major.minor>``,
the folder upstream's buildbot config reads. Each archive is checked against the SHA-256 in NVIDIA's own release
manifest (``redistrib_<version>.json``), which is kept in the folder as ``redistrib.json``: ``linux_gpu_toolkits.py``
reads the version from it. Only the parts a kernel compile needs are fetched (the compiler, its runtime headers,
NVVM, the PTX compiler and CCCL); nothing of a toolkit ships with Angelo.

``hip`` (run inside the Rocky Linux 8 build image) assembles ``<toolkits>/rocm-<v>`` from AMD's EL8 packages of the
pinned HIP. OptiX and DLSS are headers only (``optix-sdk-<v>/include``, ``dlss-sdk-<v>/include``; doc/angelo/releasing.rst,
"Linux package").
Standard library only; needs network.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gpu_sdk_pins  # noqa: E402

REDIST = "https://developer.download.nvidia.com/compute/cuda/redist/"
# What nvcc needs to compile Cycles' CUDA and OptiX kernels; names differ between CUDA 12 and 13 (``cccl``, ``cuda_crt``
# and ``libnvvm`` became their own archives in 13), so absent names are skipped.
PARTS = ("cuda_nvcc", "cuda_cudart", "cuda_cccl", "cccl", "cuda_crt", "libnvvm", "cuda_nvrtc", "libnvptxcompiler",
         "libnvjitlink", "cuda_cuobjdump", "cuda_nvdisasm", "cuda_nvprune", "cuda_profiler_api")
PLATFORM = "linux-x86_64"


def _get(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_cuda(version, toolkits, get=_get):
    """Assemble ``toolkits/cuda-<major.minor>`` for the CUDA ``version``; returns (the folder, the parts fetched)."""
    manifest_bytes = get(REDIST + "redistrib_%s.json" % version)
    manifest = json.loads(manifest_bytes)
    if manifest.get("release_label") != version:
        raise ValueError("NVIDIA's manifest for %s names release %r" % (version, manifest.get("release_label")))
    folder = Path(toolkits) / ("cuda-%s" % ".".join(version.split(".")[:2]))
    partial = folder.with_name(folder.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    fetched = []
    with tempfile.TemporaryDirectory() as scratch:
        for part in PARTS:
            entry = manifest.get(part, {}).get(PLATFORM)
            if not entry:
                continue
            data = get(REDIST + entry["relative_path"])
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("%s %s does not match NVIDIA's manifest SHA-256" % (part, entry["relative_path"]))
            archive = Path(scratch) / Path(entry["relative_path"]).name
            archive.write_bytes(data)
            with tarfile.open(archive) as handle:
                top = handle.getnames()[0].split("/", 1)[0]
                handle.extractall(scratch, filter="data")
            # Each archive holds <name>-<platform>-<version>-archive/{bin,include,lib,nvvm,...}: merge into one root.
            shutil.copytree(Path(scratch) / top, partial, symlinks=True, dirs_exist_ok=True)
            fetched.append("%s %s" % (part, manifest[part]["version"]))
    (partial / "redistrib.json").write_bytes(manifest_bytes)
    if (partial / "lib").is_dir() and not (partial / "lib64").exists():
        (partial / "lib64").symlink_to("lib")   # FindCUDA and Blender's CMake look in lib64
    if folder.exists():
        shutil.rmtree(folder)
    partial.rename(folder)
    return folder, fetched


ROCM_REPO = "https://repo.radeon.com/rocm/el8/%s/main/"
# What hipcc needs to compile Cycles' HIP kernels; dnf adds their dependencies.
HIP_PACKAGES = ("hip-devel{v}", "hipcc{v}", "rocm-llvm{v}", "rocm-device-libs{v}", "hip-runtime-amd{v}", "rocm-core{v}")


def fetch_hip(version, toolkits, run=subprocess.run):
    """Assemble ``toolkits/rocm-<version>`` from AMD's EL8 packages (run in the Rocky Linux 8 build image, which has
    dnf). dnf checks each package against the repository's metadata; only packages installing under
    ``/opt/rocm-<version>`` are unpacked (the operating system's own dependencies are the image's). Returns the
    folder and the packages unpacked."""
    folder = Path(toolkits) / ("rocm-%s" % version)
    prefix = "/opt/rocm-%s/" % version
    with tempfile.TemporaryDirectory() as scratch:
        downloads = Path(scratch) / "rpms"
        packages = [name.format(v=version) for name in HIP_PACKAGES]
        run(["dnf", "-q", "download", "--resolve", "--destdir", str(downloads),
             "--repofrompath", "angelo-rocm," + ROCM_REPO % version, "--setopt=angelo-rocm.gpgcheck=0"] + packages,
            check=True)
        unpacked = []
        root = Path(scratch) / "root"
        root.mkdir()
        for rpm in sorted(downloads.glob("*.rpm")):
            files = run(["rpm", "-qlp", str(rpm)], capture_output=True, text=True, check=True).stdout.split()
            if not any(name.startswith(prefix) for name in files):
                continue
            cpio = subprocess.Popen(["rpm2cpio", str(rpm)], stdout=subprocess.PIPE)
            run(["cpio", "-idm", "--quiet"], stdin=cpio.stdout, cwd=str(root), check=True)
            if cpio.wait() != 0:
                raise ValueError("rpm2cpio failed on %s" % rpm.name)
            unpacked.append(rpm.name)
        partial = folder.with_name(folder.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        shutil.move(str(root / prefix.strip("/")), str(partial))
    if not (partial / "bin/hipcc").exists() or not (partial / ".info/version").is_file():
        raise ValueError("the unpacked ROCm %s lacks bin/hipcc or .info/version" % version)
    if folder.exists():
        shutil.rmtree(folder)
    partial.rename(folder)
    return folder, unpacked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cuda = commands.add_parser("cuda")
    cuda.add_argument("toolkits")
    cuda.add_argument("--source", required=True)
    hip = commands.add_parser("hip", help="run inside the Rocky Linux 8 build image (dnf, rpm2cpio, cpio)")
    hip.add_argument("toolkits")
    hip.add_argument("--source", required=True)
    arguments = parser.parse_args(argv)
    pins = gpu_sdk_pins.source_pins(arguments.source, "linux")
    Path(arguments.toolkits).mkdir(parents=True, exist_ok=True)
    if arguments.command == "hip":
        folder, unpacked = fetch_hip(pins["sdks"]["hip"], arguments.toolkits)
        print("hip %s in %s: %d packages" % (pins["sdks"]["hip"], folder, len(unpacked)))
        return 0
    for version in pins["sdks"]["cuda"]:
        folder, fetched = fetch_cuda(version, arguments.toolkits)
        print("cuda %s in %s: %s" % (version, folder, ", ".join(fetched)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
