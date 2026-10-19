// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// Installer artwork rendered from the approved Angelo branding (no Blender artwork), lit like the
// splash (angelo_installer_scenes.mjs holds the compositions):
//   release/darwin/background.tif and release/darwin/buildbot/background.tif
//       1080x700 at 144 dpi, LZW TIFF (the DMG window, 540x350 points)
//   release/windows/installer_wix/WIX_UI_DIALOG.png   493x312, 8-bit RGB (the welcome/finish dialogs)
//   release/windows/installer_wix/WIX_UI_BANNER.bmp   493x58, 24-bit (the banner of the other dialogs)
// It measures the WCAG contrast of the text each host draws over them, refuses to export below the
// floors, and records sizes, checksums and the measured contrasts under "installer_art" in
// release/branding/angelo/coverage.json. Run from the repository root on macOS (Node with sharp, see
// ANGELO_NODE_MODULES in release/branding/angelo/README.md):
//   node build_files/utils/angelo_installer_art.mjs

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {fileURLToPath} from "node:url";
import {createRequire} from "node:module";
import {DMG, DMG_ARROW, WIX_BANNER, WIX_DIALOG, WIX_FACE, dmgArrowBox, dmgNameBox, dmgSvg, wixBannerSvg,
        wixDialogSvg} from "./angelo_installer_scenes.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const brand = path.join(root, "release/branding/angelo");
const COVERAGE = path.join(brand, "coverage.json");
const DMG_OUTPUTS = ["release/darwin/background.tif", "release/darwin/buildbot/background.tif"];
const DIALOG_OUTPUT = "release/windows/installer_wix/WIX_UI_DIALOG.png";
const BANNER_OUTPUT = "release/windows/installer_wix/WIX_UI_BANNER.bmp";
// WCAG 2 level AA for normal-size text; 3:1 for the cases noted where they are used.
const AA = 4.5, LEGIBLE = 3;
const BLACK = [0, 0, 0], WHITE = [255, 255, 255];
// macOS draws label text at 85% or more of full strength (see "Window chrome" in doc/angelo/app_identity.rst).
const LABEL_ALPHA = 0.85;

function loadSharp() {
  const candidates = [process.env.ANGELO_NODE_MODULES || process.env.SENSEI_NODE_MODULES || process.env.TOONLAB_NODE_MODULES,
    path.join(root, "node_modules"),
    path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")].filter(Boolean);
  for (const directory of candidates) {
    try {
      return createRequire(path.join(directory, "_angelo_loader.cjs"))("sharp");
    } catch { /* Try the next configured dependency directory. */ }
  }
  throw new Error("Set ANGELO_NODE_MODULES to a directory containing sharp");
}

// 24-bit bottom-up BMP, rows padded to four bytes (what WiX's WixUIBannerBmp expects).
function bmp24({data, info}) {
  const {width, height, channels} = info;
  const row = Math.ceil(width * 3 / 4) * 4;
  const out = Buffer.alloc(54 + row * height);
  out.write("BM", 0);
  out.writeUInt32LE(out.length, 2);
  out.writeUInt32LE(54, 10);
  out.writeUInt32LE(40, 14);
  out.writeInt32LE(width, 18);
  out.writeInt32LE(height, 22);
  out.writeUInt16LE(1, 26);
  out.writeUInt16LE(24, 28);
  out.writeUInt32LE(row * height, 34);
  out.writeInt32LE(2835, 38);
  out.writeInt32LE(2835, 42);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const source = (y * width + x) * channels;
      const target = 54 + (height - 1 - y) * row + x * 3;
      out[target] = data[source + 2];
      out[target + 1] = data[source + 1];
      out[target + 2] = data[source];
    }
  }
  return out;
}

const linear = value => (value / 255 <= 0.04045 ? value / 255 / 12.92 : ((value / 255 + 0.055) / 1.055) ** 2.4);
const luminance = ([r, g, b]) => 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
export function contrast(first, second) {
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}
const hex = color => [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16));
const twoDecimals = value => Math.floor(value * 100) / 100;  // Rounded down: a recorded contrast is never overstated.

// The pixels of `box` (left, top, right, bottom in the scene's units, `density` pixels each).
function* pixels({data, info}, box, density = 1) {
  const [left, top, right, bottom] = box.map((value, index) => (index < 2 ? Math.floor(value * density) : Math.ceil(value * density)));
  for (let y = Math.max(top, 0); y < Math.min(bottom, info.height); y++) {
    for (let x = Math.max(left, 0); x < Math.min(right, info.width); x++) {
      const offset = (y * info.width + x) * info.channels;
      yield [data[offset], data[offset + 1], data[offset + 2]];
    }
  }
}
// The lowest contrast of text in `color`, drawn at strength `alpha`, over any pixel of the boxes.
function lowestContrast(image, boxes, color, {alpha = 1, density = 1} = {}) {
  let lowest = Infinity;
  for (const box of boxes) {
    for (const pixel of pixels(image, box, density)) {
      const text = color.map((value, index) => alpha * value + (1 - alpha) * pixel[index]);
      lowest = Math.min(lowest, contrast(text, pixel));
    }
  }
  return twoDecimals(lowest);
}
function requireContrast(name, value, floor) {
  if (!(value >= floor)) throw new Error(`${name} is ${value}:1, below ${floor}:1`);
  return value;
}
const described = data => ({bytes: data.length, sha256: crypto.createHash("sha256").update(data).digest("hex")});

// Every file as `{outputs, data}` plus the record for coverage.json. Nothing is written here.
export async function installerArt(sharp = loadSharp()) {
  const lockup = fs.readFileSync(path.join(brand, "angelo-wordmark.svg"), "utf8");
  // The colour lockup is the raven mark, then the wordmark's letters in the group placed at 565, 35.
  const lettersAt = '<g transform="translate(565 35)">';
  if (!lockup.includes(lettersAt)) throw new Error("angelo-wordmark.svg no longer places its letters at 565, 35");
  const wordmark = lockup.slice(lockup.indexOf(lettersAt) + lettersAt.length, lockup.lastIndexOf("</g></svg>"));
  const selection = JSON.parse(fs.readFileSync(path.join(brand, "selection.json"), "utf8"));
  // The approved raven at the exact size it is drawn, so the SVG renderer does not resample it.
  const raven = async size => "data:image/png;base64," + (await sharp(path.join(brand, selection.master))
    .resize(size, size, {kernel: "lanczos3"}).png().toBuffer()).toString("base64");
  const raw = svg => sharp(Buffer.from(svg), {density: 72}).flatten({background: "#000000"}).raw().toBuffer({resolveWithObject: true});

  // DMG window. Lossless, so the plaques keep the exact colour the contrast is measured on.
  const density = DMG.pixels[0] / DMG.width;
  const dmgImage = sharp(Buffer.from(dmgSvg({wordmark})), {density: 72 * density}).flatten({background: "#000000"});
  const dmgPixels = await dmgImage.clone().raw().toBuffer({resolveWithObject: true});
  const dmgData = await dmgImage.clone().tiff({compression: "lzw", predictor: "horizontal", resolutionUnit: "inch",
                                              xres: DMG.dpi / 25.4, yres: DMG.dpi / 25.4}).toBuffer();
  // Finder draws the picture at its size in points, which the file's resolution decides.
  const written = await sharp(dmgData).metadata();
  if (written.width !== DMG.pixels[0] || written.height !== DMG.pixels[1] || written.density !== DMG.dpi) {
    throw new Error(`The DMG background is ${written.width}x${written.height} at ${written.density} dpi`);
  }
  const names = [dmgNameBox(DMG.app), dmgNameBox(DMG.applications)];
  const nameContrast = (color, alpha) => lowestContrast(dmgPixels, names, color, {alpha, density});
  // The wall the chevrons stand on: a band above and one below them, clear of their antialiased tips.
  const [left, top, right, bottom] = dmgArrowBox();
  const wall = [[left, top - 5, right, top - 1], [left, bottom + 1, right, bottom + 5]];
  const dmg = {
    outputs: DMG_OUTPUTS, format: "TIFF, LZW, 8-bit RGB", pixels: DMG.pixels, dpi: DMG.dpi, ...described(dmgData),
    window: {bounds: DMG.window, visible_height: DMG.visibleHeight, icon_size: DMG.iconSize, text_size: DMG.textSize,
             app: DMG.app, applications: DMG.applications, below: DMG.below},
    contrast: {
      // Finder draws the names black in a window with a background picture.
      name_black: requireContrast("A black name on its plaque", nameContrast(BLACK, 1), AA),
      name_black_at_85: requireContrast("A black name at 85% on its plaque", nameContrast(BLACK, LABEL_ALPHA), AA),
      // Not what Finder does today; the plaque keeps such a name legible all the same.
      name_white: requireContrast("A white name on its plaque", nameContrast(WHITE, 1), LEGIBLE),
      name_white_at_85: requireContrast("A white name at 85% on its plaque", nameContrast(WHITE, LABEL_ALPHA), LEGIBLE),
      // A graphic, not text: WCAG asks 3:1 of it.
      arrow: requireContrast("The dimmest chevron on the wall", Math.min(...DMG_ARROW.colors.map(
        color => lowestContrast(dmgPixels, wall, hex(color), {density}))), LEGIBLE),
    },
  };

  const dialogPixels = await raw(wixDialogSvg({artwork: await raven(WIX_DIALOG.mark.size), wordmark}));
  const {width, height, channels} = dialogPixels.info;
  const dialogData = await sharp(dialogPixels.data, {raw: {width, height, channels}}).png({compressionLevel: 9}).toBuffer();
  const face = hex(WIX_FACE);
  for (const pixel of pixels(dialogPixels, WIX_DIALOG.checkBox)) {
    if (pixel.some((value, index) => value !== face[index])) throw new Error("The check box area is not the dialog colour");
  }
  const dialog = {
    outputs: [DIALOG_OUTPUT], format: "PNG, 8-bit RGB", pixels: [WIX_DIALOG.width, WIX_DIALOG.height], ...described(dialogData),
    contrast: {
      text_black: requireContrast("Dialog text", lowestContrast(dialogPixels, WIX_DIALOG.text, BLACK), AA),
      check_box_black: requireContrast("The check box's text", lowestContrast(dialogPixels, [WIX_DIALOG.checkBox], BLACK), AA),
    },
  };

  const bannerPixels = await raw(wixBannerSvg({artwork: await raven(WIX_BANNER.mark.size)}));
  const bannerData = bmp24(bannerPixels);
  const banner = {
    outputs: [BANNER_OUTPUT], format: "BMP, 24-bit", pixels: [WIX_BANNER.width, WIX_BANNER.height], ...described(bannerData),
    contrast: {text_black: requireContrast("Banner text", lowestContrast(bannerPixels, WIX_BANNER.text, BLACK), AA)},
  };

  return {
    files: [{outputs: DMG_OUTPUTS, data: dmgData}, {outputs: [DIALOG_OUTPUT], data: dialogData}, {outputs: [BANNER_OUTPUT], data: bannerData}],
    record: {source: "build_files/utils/angelo_installer_scenes.mjs", dmg_background: dmg, wix_dialog: dialog, wix_banner: banner},
  };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const {files, record} = await installerArt();
  for (const {outputs, data} of files) {
    for (const output of outputs) fs.writeFileSync(path.join(root, output), data);
  }
  // angelo_branding.mjs owns the rest of the file and keeps this entry; it is always the last one.
  const coverage = JSON.parse(fs.readFileSync(COVERAGE, "utf8"));
  delete coverage.installer_art;
  fs.writeFileSync(COVERAGE, JSON.stringify({...coverage, installer_art: record}, null, 2) + "\n");
  console.log("Angelo installer artwork exported.");
}
