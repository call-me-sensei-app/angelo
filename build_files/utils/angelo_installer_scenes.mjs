// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The installer artwork's SVG sources: the DMG window background and the two
// WiX bitmaps, lit like the splash. The night wall, the planes, the bloom and
// the grain are `facetedLight` of angelo_splash.mjs, composed again for each
// canvas; the flat shapes are the facets of the wordmark's "A". Pure string
// building; angelo_installer_art.mjs renders, checks and writes the files.
//
// Each piece is drawn around text its host puts on it, in a colour the artwork
// does not choose, so each exports its geometry and the boxes that text falls in:
//
// * Finder draws an icon's name below it, black in a window that has a
//   background picture. Each name gets a plaque in the A's mid teal, which
//   carries black at AA and would still carry white.
// * Windows Installer draws its dialog text in the default (black) text colour
//   over the bitmaps, so they stay paper white wherever a text control lies.

import {FACETS} from "./angelo_wordmark.mjs";
import {LEAN, lean, facetedLight, grain, shadow} from "./angelo_splash.mjs";

const round = value => Math.round(value * 1000) / 1000;
const facet = name => FACETS.find(entry => entry.name === name).color;
const polygon = points => "M" + points.map(([x, y]) => `${round(x)} ${round(y)}`).join("L") + "Z";
const open = (width, height) => '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
  `width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`;
// The wordmark's letters are 1425 units wide, with the baseline at 380.
const WORDMARK_WIDTH = 1425, WORDMARK_BASELINE = 380;
// The wordmark about `width` wide on `baseline`, its A's left foot `gap` to the right of the seam:
// the seam runs up beside the A's left leg, as on the splash.
const besideSeam = (seam, {width, baseline, gap}, wordmark) => {
  const scale = round(width / WORDMARK_WIDTH), x = seam[0] + LEAN * (seam[1] - baseline) + gap;
  return `<g transform="translate(${round(x)} ${round(baseline - WORDMARK_BASELINE * scale)}) scale(${scale})">${wordmark}</g>`;
};

// ---------------------------------------------------------------------------
// DMG window background. Units are Finder points; the file is twice that
// (144 dpi), as upstream Blender's is. The window, the icon size and the two
// icon positions are upstream's own DMG layout, which
// release/darwin/dmg_window.applescript.in applies.

export const DMG = {
  width: 540, height: 350, pixels: [1080, 700], dpi: 144,
  // Finder bounds of the window, title bar included. The title bar has been 22 to 32 points tall
  // (32 on macOS 26), so the window shows the top 340 to 350 points: the rest is bleed.
  window: [100, 100, 640, 472], visibleHeight: 340,
  iconSize: 128, textSize: 12,
  app: [135, 190], applications: [400, 190],
  below: [270, 500],  // Any other file on the volume: under the picture, out of the window.
};
// A 12-point name under a 128-point icon: the box upstream's background allots to it.
const NAME = {top: 74, bottom: 91, half: 46};
const PLAQUE = {top: 71, bottom: 96, half: 70, end: 16};
export const dmgNameBox = ([x, y]) => [x - NAME.half, y + NAME.top, x + NAME.half, y + NAME.bottom];

// The seam passes between the two icons; the raven the user drags is on the lit plane.
const DMG_SCENE = {
  width: DMG.width, height: DMG.height,
  seam: [262, 190], lit: [0, 0, 250, 330], calm: [250, 0, 520, 350],
  edge: [560, 190], far: [540, 120, 440, 350],
  bloom: [DMG.app[0], DMG.app[1] - 6, 190],
  vignette: 380, squash: 0.72,
};
// Three facets lit from the left, their ends leaning with the seam. The name sits on the middle one,
// the one teal of the A that carries black at AA and white above 4:1.
function plaque([x, y]) {
  const top = y + PLAQUE.top, bottom = y + PLAQUE.bottom, shift = LEAN * (bottom - top) / 2;
  const band = (left, right, color) => `<path fill="${color}" d="${polygon(
    [[left + shift, top], [right + shift, top], [right - shift, bottom], [left - shift, bottom]])}"/>`;
  const [left, right] = [x - PLAQUE.half, x + PLAQUE.half];
  return band(left, right, facet("right")) + band(left, left + PLAQUE.end, facet("left-foot")) +
    band(right - PLAQUE.end, right, facet("right-foot"));
}
// "Drag it there": three chevrons with the A's own apex angle, brightening towards the folder.
export const DMG_ARROW = {x: 236, y: 190, length: 17, thickness: 6, step: 21, colors: ["left-foot", "left", "apex"].map(facet)};
function arrow() {
  const {x, y, length, thickness, step, colors} = DMG_ARROW, half = length * LEAN;
  return colors.map((color, index) => {
    const left = x + index * step;
    return `<path fill="${color}" d="${polygon([[left, y - half], [left + thickness, y - half], [left + thickness + length, y],
                                               [left + thickness, y + half], [left, y + half], [left + length, y]])}"/>`;
  }).join("");
}
export const dmgArrowBox = () => {
  const {x, y, length, thickness, step, colors} = DMG_ARROW;
  return [x, y - length * LEAN, x + (colors.length - 1) * step + thickness + length, y + length * LEAN];
};

// `wordmark` is the faceted A and the "ngelo" outlines as the wordmark files carry them. The app's
// own icon is the raven here, so the picture carries the wordmark alone, beside the seam as on the splash.
export function dmgSvg({wordmark}) {
  const {defs, layers} = facetedLight(DMG_SCENE);
  return open(DMG.width, DMG.height) + `<defs>${defs}${grain(DMG.width, DMG.height)}</defs>` +
    `<g filter="url(#grain)">${layers}</g>` + plaque(DMG.app) + plaque(DMG.applications) + arrow() +
    besideSeam(DMG_SCENE.seam, {width: 168, baseline: 88, gap: 20}, wordmark) + "</svg>\n";
}

// ---------------------------------------------------------------------------
// WiX bitmaps. Units are pixels at 96 dpi, 4/3 of the installer units the WiX 3
// dialogs are laid out in (370 x 270): WelcomeDlg and ExitDialog put the dialog
// bitmap at 0, 0, 370 x 234 and InstallDirDlg and the other pages put the
// banner at 0, 0, 370 x 44. Their text controls are transparent and have no
// colour in WixUI_Angelo.wxs, so the text is the default black.

const unit = value => round(value * 4 / 3);
const box = (x, y, width, height) => [unit(x), unit(y), unit(x + width), unit(y + height)];
const WIX_PAPER = "#ffffff";
export const WIX_FACE = "#f0f0f0";  // COLOR_3DFACE, the dialog colour below the bitmap, in the default Windows theme.

export const WIX_DIALOG = {
  width: 493, height: 312, panel: 164,
  mark: {x: 12, y: 36, size: 112},
  // Title, then the description of WelcomeDlg (80 to 140) and ExitDialog (70 to 110).
  text: [box(135, 20, 220, 60), box(135, 70, 220, 70)],
  // ExitDialog's check box paints the dialog colour behind itself.
  checkBox: box(135, 190, 220, 40),
};
export function wixDialogSvg({artwork, wordmark}) {
  const {width, height, panel, mark} = WIX_DIALOG;
  const letters = {width: 98, baseline: 262, gap: 9};
  // The seam crosses the tall panel from the top right to the bottom left: the raven above it, the
  // wordmark below, 12 pixels from the panel's right edge.
  const seam = [panel - 12 - letters.width - letters.gap, letters.baseline];
  const {defs, layers} = facetedLight({
    width: panel, height, seam, lit: [0, 0, 120, 250], calm: [60, 60, 200, 330],
    bloom: [mark.x + mark.size / 2, mark.y + mark.size / 2 + 4, 150], vignette: 230, squash: 1.5});
  // The page settles into the dialog colour where the check box and the button row follow.
  const settle = '<linearGradient id="paper" gradientUnits="userSpaceOnUse" x1="0" y1="196" x2="0" y2="250">' +
    `<stop offset="0" stop-color="${WIX_PAPER}"/><stop offset="1" stop-color="${WIX_FACE}"/></linearGradient>`;
  return open(width, height) +
    `<defs>${defs}${grain(panel, height)}${shadow(mark.size)}${settle}<clipPath id="panel"><rect width="${panel}" height="${height}"/></clipPath></defs>` +
    `<rect width="${width}" height="${height}" fill="url(#paper)"/>` +
    `<g clip-path="url(#panel)"><g filter="url(#grain)">${layers}</g>` +
    `<image x="${mark.x}" y="${mark.y}" width="${mark.size}" height="${mark.size}" filter="url(#shadow)" xlink:href="${artwork}"/>` +
    besideSeam(seam, letters, wordmark) + "</g></svg>\n";
}

export const WIX_BANNER = {
  width: 493, height: 58,
  mark: {x: 450, y: 14, size: 36},
  text: [box(15, 6, 200, 15), box(25, 23, 280, 15)],  // Title and description.
  edge: 418,  // Where the cap's leaning edge meets the bottom of the banner.
};
// A thin strip cannot hold the splash: the text gets the paper, and the right end is a cap of the night
// wall with the raven, cut on the A's lean and edged with three of its facets, light to dark.
export function wixBannerSvg({artwork}) {
  const {width, height, mark, edge} = WIX_BANNER;
  const [top, bottom] = lean(edge, height, height);
  const band = (from, to, color) => `<path fill="${color}" d="${polygon(
    [[top + from, 0], [top + to, 0], [bottom + to, height], [bottom + from, height]])}"/>`;
  const bands = [[0, 2.5, "apex"], [2.5, 5.5, "left"], [5.5, 9.5, "right"]], wall = 9.5;
  // The cap is a canvas of its own, all lit plane: its seam is the cut itself, so the scene's is out of sight.
  const cap = width - edge;
  const {defs, layers} = facetedLight({
    width: cap, height, seam: [cap + 40, 0], lit: [0, 0, 70, 70], calm: [60, 0, 120, 58],
    bloom: [mark.x + mark.size / 2 - edge, mark.y + mark.size / 2 + 2, 60], vignette: 90, squash: 1});
  return open(width, height) +
    `<defs>${defs}${grain(cap, height)}${shadow(mark.size)}<clipPath id="cap"><path d="${polygon(
      [[top + wall, 0], [width, 0], [width, height], [bottom + wall, height]])}"/></clipPath></defs>` +
    `<rect width="${width}" height="${height}" fill="${WIX_PAPER}"/>` +
    `<g clip-path="url(#cap)"><g transform="translate(${edge} 0)" filter="url(#grain)">${layers}</g></g>` +
    bands.map(([from, to, name]) => band(from, to, facet(name))).join("") +
    `<image x="${mark.x}" y="${mark.y}" width="${mark.size}" height="${mark.size}" filter="url(#shadow)" xlink:href="${artwork}"/></svg>\n`;
}
