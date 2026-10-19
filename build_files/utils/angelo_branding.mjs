// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// SVG-native UI branding and deterministic exports. Does not edit the approved bitmap.
// The raven mark is unchanged from the previous name; only the wordmark is new
// (the faceted A in angelo_wordmark.mjs, the "ngelo" outlines from
// angelo_letters.mjs). Run from the repository root on macOS:
//   node build_files/utils/angelo_branding.mjs

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {fileURLToPath} from "node:url";
import {createRequire} from "node:module";
import {A_HEIGHT, facetedA} from "./angelo_wordmark.mjs";
import {LETTERS, OUTPUT as LETTERS_SVG, fontSha256} from "./angelo_letters.mjs";
import {SPLASH_PIXELS, splashSvg} from "./angelo_splash.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const brand = path.join(root, "release/branding/angelo");
const output = path.join(brand, "exports");
// ANGELO_NODE_MODULES wins; SENSEI_ and TOONLAB_NODE_MODULES are the deprecated pre-rename names.
const candidates = [process.env.ANGELO_NODE_MODULES || process.env.SENSEI_NODE_MODULES || process.env.TOONLAB_NODE_MODULES,
  path.join(root, "node_modules"),
  path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")].filter(Boolean);
let sharp;
for (const directory of candidates) {
  try {
    sharp = createRequire(path.join(directory, "_angelo_loader.cjs"))("sharp");
    break;
  } catch { /* Try the next configured dependency directory. */ }
}
if (!sharp) throw new Error("Set ANGELO_NODE_MODULES to a directory containing sharp");
fs.mkdirSync(output, {recursive: true});
const white = fs.readFileSync(path.join(brand, "angelo-mark-white.svg"), "utf8");
const body = white.slice(white.indexOf("<g "), white.lastIndexOf("</svg>"));
const black = white.replace(/#ffffff/g, "#111318");
const svg = (w, h, content) => `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${content}</svg>\n`;
const save = (filename, value) => fs.writeFileSync(path.join(root, filename), value);
const selection = JSON.parse(fs.readFileSync(path.join(brand, "selection.json"), "utf8"));
const master = path.resolve(brand, selection.master);
if (path.dirname(master) !== brand) throw new Error("Selected bitmap must be in the branding directory");
const artwork = "data:image/png;base64," + fs.readFileSync(master).toString("base64");
save("release/branding/angelo/angelo-mark-dark.svg", black);
save("release/datafiles/icons_svg/blender.svg", white);
save("release/freedesktop/icons/symbolic/apps/blender-symbolic.svg", black);

// The canonical colored reduction has its own teal shell, visor, eyes and beak.
// Keep pure paths/gradients for NanoSVG: don't recolor every component blue.
const colorMark = fs.readFileSync(path.join(brand, "angelo-mark-color.svg"), "utf8");
if (/<(?:image|text)\b/.test(colorMark)) {
  throw new Error("The native color mark must use paths/gradients, not images or text");
}
const colorBody = colorMark.slice(colorMark.indexOf("<defs>"), colorMark.lastIndexOf("</svg>"));
const desktop = svg(1024, 1024, `<image width="1024" height="1024" xlink:href="${artwork}"/>`);
save("release/freedesktop/icons/scalable/apps/blender.svg", desktop);
save("release/freedesktop/icons/scalable/apps/angelo.svg", desktop);
save("release/freedesktop/icons/symbolic/apps/angelo-symbolic.svg", black);

// Wordmark: the faceted "A" followed by "ngelo" as in the approved source: the
// project's OFL Inter at weight 500 with -0.015 em letter spacing, outlined for the
// native SVG renderer (committed by angelo_letters.mjs). Layout follows the same
// source: the A is 0.76 em tall on the baseline, with a 0.02 em gap before the "n".
const FONT_SIZE = LETTERS.fontSize, BASELINE = LETTERS.baseline;
const letters = fs.readFileSync(path.join(root, LETTERS_SVG), "utf8");
if (!letters.includes(`data-font-sha256="${fontSha256()}"`)) {
  throw new Error("The bundled Inter changed; rerun node build_files/utils/angelo_letters.mjs first");
}
const ngeloPaths = letters.slice(letters.indexOf("<path"), letters.lastIndexOf("</svg>"));
// Right edge of the outlines: each glyph path is translated to its pen position and
// uses absolute M/L/C commands, so every even coordinate is an x value.
const ngeloRight = Math.max(...[...ngeloPaths.matchAll(/<path\b[^>]*>/g)].flatMap(([element]) => {
  const offset = Number(element.match(/translate\(([-\d.]+)/)?.[1] || 0);
  const outline = element.match(/ d="([^"]*)"/)[1];
  if (/[^MLCZ\d\s.eE-]/.test(outline)) throw new Error("Unexpected path command in the outlined letters");
  return [...outline.matchAll(/-?[\d.]+(?:e-?\d+)?/g)].filter((_, i) => i % 2 === 0).map(x => offset + Number(x));
}));
const round = value => Math.round(value * 1000) / 1000;  // Keep the SVG numbers readable.
const aScale = round(0.76 * FONT_SIZE / A_HEIGHT);
const aTop = round(BASELINE - A_HEIGHT * aScale);
const ngeloLeft = round(100 * aScale + 0.02 * FONT_SIZE);
const lettersWidth = ngeloLeft + ngeloRight;
const word = aContent => `<g transform="translate(0 ${aTop}) scale(${aScale})">${aContent}</g>` +
  `<g transform="translate(${ngeloLeft} 0)">${ngeloPaths}</g>`;
// Same mark and letter placement as the previous 2000 x 560 lockup, wider for "Angelo".
const wordmarkWidth = Math.ceil((565 + lettersWidth + 35) / 10) * 10;
const wordmarkWhite = svg(wordmarkWidth, 560, `<g transform="translate(0 20) scale(.405)">${body}</g>` +
  `<g transform="translate(565 35)">${word(facetedA({mono: "#ffffff"}))}</g>`);
const wordmarkColor = svg(wordmarkWidth, 560, `<g transform="translate(0 20) scale(.405)">${colorBody}</g>` +
  `<g transform="translate(565 35)">${word(facetedA())}</g>`);
save("release/branding/angelo/angelo-wordmark-white.svg", wordmarkWhite);
save("release/branding/angelo/angelo-wordmark.svg", wordmarkColor);
// The About dialog draws this single-colour version tinted by the theme.
save("release/datafiles/icons_svg/blender_logo_large.svg", wordmarkWhite);
const fileGlyph = svg(1600, 1600,
  '<path fill="none" stroke="#ffffff" stroke-opacity=".6" stroke-width="90" stroke-linejoin="round" d="M1150 120H540L180 480V1470H1390V520 M180 480H540V120"/>' +
  `<g transform="translate(600 620) scale(.58)">${body}</g>`);
save("release/datafiles/icons_svg/file_blend.svg", fileGlyph);
const document = svg(1024, 1024,
  '<path fill="#e8edf8" d="M245 72H640L819 252V953H205V112Q205 72 245 72Z"/>' +
  '<path fill="#96a7c6" d="M640 72V252H819Z"/>' +
  `<image x="235" y="334" width="550" height="550" xlink:href="${artwork}"/>`);
save("release/branding/angelo/angelo-document.svg", document);

const render = async (source, filename, width, height=width) => {
  await sharp(Buffer.from(source), {density: 144}).resize(width, height).png().toFile(filename);
};
for (const size of [16, 20, 24, 32, 48, 64, 128, 256, 512, 1024]) {
  await render(white, path.join(output, `angelo-white-${size}.png`), size);
  await render(black, path.join(output, `angelo-dark-${size}.png`), size);
}
const wordmarkPixels = [Math.round(wordmarkWidth * 0.6), 336];
await render(wordmarkWhite, path.join(output, "angelo-wordmark-white.png"), ...wordmarkPixels);
await render(wordmarkColor, path.join(output, "angelo-wordmark.png"), ...wordmarkPixels);
for (const size of [16, 24, 32, 48, 64, 128, 256, 512, 1024]) {
  await render(document, path.join(output, `angelo-document-${size}.png`), size);
}
// The splash keeps the lockup (the approved bitmap mark on the left, then the coloured
// faceted A with white "ngelo") on the lit background of angelo_splash.mjs.
const splash = splashSvg({artwork, wordmark: word(facetedA())});
const splashPng = path.join(root, "release/datafiles/splash.png");
save("release/branding/angelo/angelo-splash.svg", splash);
await render(splash, splashPng, ...SPLASH_PIXELS);
const splashSha256 = crypto.createHash("sha256").update(fs.readFileSync(splashPng)).digest("hex");

// Keep MSIX resource names/dimensions, replace artwork including contrast variants.
const walk = directory => fs.readdirSync(directory, {withFileTypes:true}).flatMap(entry =>
  entry.isDirectory() ? walk(path.join(directory, entry.name)) : [path.join(directory, entry.name)]);
let msixCount = 0;
for (const file of walk(path.join(root, "release/windows/msix/Assets"))) {
  if (!file.endsWith(".png")) continue;
  const name = path.basename(file);
  const square = name.match(/^Square(\d+)x(\d+)Logo/);
  const wide = name.match(/^Wide(\d+)x(\d+)Logo/);
  const target = name.match(/targetsize-(\d+)/);
  const scale = Number(name.match(/scale-(\d+)/)?.[1] || 100) / 100;
  const base = square || wide;
  const w = target ? Number(target[1]) : Math.round((base ? Number(base[1]) : 50) * scale);
  const h = target ? w : Math.round((base ? Number(base[2]) : 50) * scale);
  const side = Math.min(w,h);
  const contrast = file.includes("contrast-black") ? white : file.includes("contrast-white") ? black : null;
  const content = contrast
    ? `<g transform="translate(${(w-side)/2} ${(h-side)/2}) scale(${side/1254})">${contrast.slice(contrast.indexOf("<g "), contrast.lastIndexOf("</svg>"))}</g>`
    : `<image x="${(w-side)/2}" y="${(h-side)/2}" width="${side}" height="${side}" xlink:href="${artwork}"/>`;
  await render(svg(w,h,content), file, w,h);
  msixCount++;
}

// X11 embeds _NET_WM_ICON ARGB words. Decode, never recolor/crop the approved icon PNGs.
const x11 = [];
for (const size of [16,32,48,64]) {
  const {data,info} = await sharp(path.join(output, `angelo-${size}.png`)).ensureAlpha().raw().toBuffer({resolveWithObject:true});
  if(info.channels !== 4) throw new Error("RGBA icon expected");
  x11.push(`${size}, ${size},`);
  let values = [];
  for (let i=0;i<data.length;i+=4) {
    const argb = (((data[i+3]<<24)>>>0) | (data[i]<<16) | (data[i+1]<<8) | data[i+2])>>>0;
    values.push("0x" + argb.toString(16));
    if (values.length===8) { x11.push("    "+values.join(", ")+","); values=[]; }
  }
  if(values.length) x11.push("    "+values.join(", ")+",");
}
save("intern/ghost/intern/GHOST_IconX11.hh",
  // The upstream file's notice stays: the generated pixels replace its artwork, not its attribution.
  "/* SPDX-FileCopyrightText: 2001-2002 NaN Holding BV. All rights reserved.\n" +
  " * SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.\n *\n * SPDX-License-Identifier: GPL-2.0-or-later */\n" +
  "/** Generated from approved Angelo icon PNGs by build_files/utils/angelo_branding.mjs. */\n#pragma once\n" +
  "/* Legacy symbol kept for GHOST ABI/source compatibility. Artwork is Angelo. */\n" +
  "static const unsigned long BLENDER_ICONS_WM_X11[] = {\n"+x11.join("\n")+"\n};\n");
const inventory = {status:"source_wired", artwork:selection.concept, master:selection.master, white_vector:"angelo-mark-white.svg", color_vector:"angelo-mark-color.svg",
  wordmark:"angelo-wordmark.svg", wordmark_white:"angelo-wordmark-white.svg", wordmark_style:"Option B: faceted teal triangular A, then ngelo", wordmark_font:"Project Inter, OFL-1.1",
  surfaces:["top-left menu/RNA branding glyphs", "About wordmark", "blend-file glyphs", "splash", "macOS app/document icons", "Windows app/document/installers", "MSIX including contrast", "Linux desktop/symbolic", "Wayland window", "X11 window"],
  splash:{source:"angelo-splash.svg", output:"release/datafiles/splash.png", pixels:SPLASH_PIXELS, sha256:splashSha256},
  msix_assets:msixCount, compiled_runtime_requires_rebuild:true,
  // The installer artwork's entry belongs to angelo_installer_art.mjs: carried over, never written here.
  installer_art:JSON.parse(fs.readFileSync(path.join(brand, "coverage.json"), "utf8")).installer_art};
save("release/branding/angelo/coverage.json", JSON.stringify(inventory,null,2)+"\n");
console.log(`Angelo UI vectors, native window data and ${msixCount} MSIX assets exported.`);
