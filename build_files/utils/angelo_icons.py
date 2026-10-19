# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Package the selected, unchanged artwork. No generation, tracing or recoloring.

Run on macOS: python3 build_files/utils/angelo_icons.py
Uses native sips for resolution exports and iconutil for the macOS container.
The ICO writer only packages those PNGs, without manipulating image pixels.
"""

import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "release/branding/angelo"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("The selected master must be a PNG, not an LFS pointer")
    return struct.unpack(">II", data[16:24])


def write_ico(path, pngs):
    offset = 6 + 16 * len(pngs)
    entries, images = [], []
    for size, data in pngs:
        if png_size(data) != (size, size) or not 1 <= size <= 256:
            raise ValueError("ICO requires matching square PNG frames up to 256px")
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        images.append(data)
        offset += len(data)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(pngs)) + b"".join(entries + images))


def main():
    for command in ("sips", "iconutil"):
        if not shutil.which(command):
            raise SystemExit(f"{command} is required; run icon packaging on macOS")
    selection = json.loads((BRAND / "selection.json").read_text())
    master = (BRAND / selection["master"]).resolve()
    if not master.is_relative_to(BRAND.resolve()) or selection["status"] not in {"selected_provisional", "selected_final"}:
        raise SystemExit("Select a master inside the Angelo branding directory first")
    width, height = png_size(master.read_bytes())
    if width != height or width < 1024:
        raise SystemExit("Use a square master of at least 1024px")
    output = BRAND / "exports"
    output.mkdir(exist_ok=True)
    shutil.copyfile(master, output / "angelo-logo.png")
    for size in SIZES:
        subprocess.run(["sips", "-z", str(size), str(size), str(master), "--out", str(output / f"angelo-{size}.png")],
                       check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix="angelo-icons-") as temporary:
        iconset = Path(temporary) / "Angelo.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                shutil.copyfile(output / f"angelo-{size * scale}.png", iconset / f"icon_{size}x{size}{suffix}.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output / "Angelo.icns")], check=True)
    write_ico(output / "angelo.ico", [(size, (output / f"angelo-{size}.png").read_bytes()) for size in SIZES if size <= 256])
    if (output / "angelo-document-1024.png").is_file():
        with tempfile.TemporaryDirectory(prefix="angelo-document-") as temporary:
            iconset = Path(temporary) / "AngeloDocument.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    suffix = "@2x" if scale == 2 else ""
                    shutil.copyfile(output / f"angelo-document-{size * scale}.png", iconset / f"icon_{size}x{size}{suffix}.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output / "AngeloDocument.icns")], check=True)
        write_ico(output / "angelo-file.ico", [(size, (output / f"angelo-document-{size}.png").read_bytes()) for size in SIZES if size <= 256])
        shutil.copyfile(output / "angelo.ico", ROOT / "release/windows/icons/angelo.ico")
        shutil.copyfile(output / "angelo-file.ico", ROOT / "release/windows/icons/angelo-file.ico")
    manifest = {"concept": selection["concept"], "master": selection["master"],
                "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                "png_sizes": list(SIZES), "generator": "Native sips/iconutil; unchanged selected artwork",
                "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(output.iterdir()) if path.suffix in {".png", ".icns", ".ico"}}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Angelo selected logo packaged:", selection["concept"])


if __name__ == "__main__":
    main()
