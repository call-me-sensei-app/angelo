// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The splash image's SVG source: the approved lockup (bitmap raven, faceted-A
// wordmark) on "Faceted light", a background built from the wordmark's own
// teals. Two planes meet on a seam that runs parallel to the A's left leg: the
// raven sits on the lit plane, the wordmark on the calm one. Pure string
// building, no dependencies; angelo_branding.mjs renders it with sharp/librsvg.
//
// The splash is a PNG, so (unlike the wordmarks NanoSVG draws) this source may
// use gradients and filters. It must stay font-free and deterministic: the
// grain is feTurbulence with a fixed seed.
//
// Blender draws the image 45 widget units wide (495 px at scale 1, 990 px on a
// Retina display) with rounded top corners, the version label in white at the
// top right, and the menu panel (#181818 in the default theme) directly below.
// So the top right stays dark, and the bottom edge settles into the panel tone.
//
// The background is `facetedLight`, which takes a canvas and the places of its
// seams and bloom: angelo_installer_scenes.mjs lights the installer artwork with
// the same planes, colours and grain, so a change here changes the family.

export const SPLASH_WIDTH = 1000;
export const SPLASH_HEIGHT = 500;
// Rendered at twice the user units (Blender's own splash is 1000 x 500), so the
// lockup is scaled down, never up, until the interface scale passes 4.
export const SPLASH_PIXELS = [2000, 1000];

const W = SPLASH_WIDTH, H = SPLASH_HEIGHT;
// The lockup keeps the previous splash's placement and proportions.
const RAVEN = {x: 50, y: 95, size: 300};
const WORDMARK = "translate(415 175) scale(.34)";
const PANEL = "#181818";  // wcol_menu_back.inner of the default theme.
// The A's legs lean 50 across for 88 down (angelo_wordmark.mjs); the seams lean with them.
export const LEAN = 50 / 88;
const round = value => Math.round(value * 1000) / 1000;
// A line with the A's left-leg lean through (x, y): its x at the top and at the bottom of a canvas `height` tall.
export const lean = (x, y, height) => [round(x + LEAN * y), round(x - LEAN * (height - y))];

const stops = list => list.map(([offset, color, opacity = 1]) =>
  `<stop offset="${offset}" stop-color="${color}" stop-opacity="${opacity}"/>`).join("");
const radial = (id, cx, cy, r, list, transform = "") =>
  `<radialGradient id="${id}" gradientUnits="userSpaceOnUse" cx="${cx}" cy="${cy}" r="${r}"` +
  `${transform ? ` gradientTransform="${transform}"` : ""}>${stops(list)}</radialGradient>`;
const linear = (id, x1, y1, x2, y2, list) =>
  `<linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}">${stops(list)}</linearGradient>`;

// Where the splash puts the light. `seam` and `edge` are points on the two
// seams, the four-number entries are gradient vectors, `bloom` is a centre and
// a radius, `foot` the reach of the dark corner plane along the bottom edge.
const SPLASH_SCENE = {
  width: W, height: H,
  seam: [382, 250], lit: [0, 0, 420, 460], calm: [380, 0, 860, 500],  // Between the raven and the wordmark.
  edge: [948, 250], far: [1000, 140, 800, 500],  // Right of the wordmark; only the lower right corner sees it.
  foot: 216, footFade: [0, 500, 190, 250],
  bloom: [200, 240, 380],
  vignette: 640, squash: 0.62,
  settle: 120, panel: PANEL,
};

// "Faceted light" for a canvas of any size, as `{defs, layers}`: the night wall,
// the planes in the facet teals (lit from the upper left), a bloom, the seam
// catching the light as an edge of the A does, then the vignette and, where a
// panel follows below the image, the settle into its colour. `edge`, `foot` and
// `settle` are optional. The layers go under the grain filter.
export function facetedLight(scene) {
  const {width, height, seam, lit, calm, bloom} = scene;
  const fill = id => `<rect width="${width}" height="${height}" fill="url(#${id})"/>`;
  const [seamTop, seamBottom] = lean(...seam, height);
  let defs =
    linear("wall", 0, 0, width, height, [[0, "#082229"], [0.5, "#07151a"], [1, "#06090c"]]) +
    linear("plane-lit", ...lit, [[0, "#1aa3ad", 0.46], [0.55, "#087787", 0.34], [1, "#087787", 0.10]]) +
    linear("plane-calm", ...calm, [[0, "#0b8a99", 0.22], [0.6, "#087787", 0.08], [1, "#087787", 0.02]]);
  let layers =
    fill("wall") +
    `<path fill="url(#plane-lit)" d="M0 0H${seamTop}L${seamBottom} ${height}H0Z"/>` +
    `<path fill="url(#plane-calm)" d="M${seamTop} 0H${width}V${height}H${seamBottom}Z"/>`;
  if (scene.edge) {
    const [edgeTop, edgeBottom] = lean(...scene.edge, height);
    defs += linear("plane-far", ...scene.far, [[0, "#087787", 0], [1, "#0b8a99", 0.16]]);
    layers += `<path fill="url(#plane-far)" d="M${width} ${round((edgeTop - width) / LEAN)}V${height}H${edgeBottom}Z"/>`;
  }
  if (scene.foot) {
    defs += linear("plane-foot", ...scene.footFade, [[0, "#04363d", 0.9], [1, "#04363d", 0]]);
    layers += `<path fill="url(#plane-foot)" d="M0 ${round(height - scene.foot / LEAN)}L${scene.foot} ${height}H0Z"/>`;
  }
  defs +=
    radial("bloom", ...bloom, [[0, "#27bdbf", 0.36], [0.5, "#0b8a99", 0.13], [1, "#087787", 0]]) +
    linear("ridge", seamTop, 0, seamBottom, height, [[0, "#a1f4d6", 0.26], [0.5, "#a1f4d6", 0.11], [1, "#a1f4d6", 0]]);
  layers +=
    fill("bloom") +
    `<path fill="none" stroke="url(#ridge)" stroke-width="1.5" d="M${seamTop} 0L${seamBottom} ${height}"/>`;
  // Vignette, then the settle into the panel below the image.
  const [cx, cy] = [width / 2, height / 2];
  defs += radial("vignette", cx, cy, scene.vignette, [[0.45, "#000000", 0], [1, "#000000", 0.62]],
                 `translate(${cx} ${cy}) scale(1 ${scene.squash}) translate(${-cx} ${-cy})`);
  layers += fill("vignette");
  if (scene.settle) {
    defs += linear("settle", 0, height - scene.settle, 0, height, [[0, scene.panel, 0], [1, scene.panel, 0.92]]);
    layers += fill("settle");
  }
  return {defs, layers};
}

// Film grain, centred on zero so it neither lifts nor crushes the tones. It also
// dithers the dark gradients, which would otherwise band in 8 bits.
const GRAIN_AMOUNT = 0.05;
export const grain = (width, height) =>
  `<filter id="grain" filterUnits="userSpaceOnUse" x="0" y="0" width="${width}" height="${height}" color-interpolation-filters="sRGB">` +
  '<feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="11" stitchTiles="noStitch" result="noise"/>' +
  '<feColorMatrix in="noise" type="matrix" values="0.34 0.33 0.33 0 0  0.34 0.33 0.33 0 0  0.34 0.33 0.33 0 0  0 0 0 0 1" result="mono"/>' +
  `<feComposite in="SourceGraphic" in2="mono" operator="arithmetic" k1="0" k2="1" k3="${GRAIN_AMOUNT}" k4="${-GRAIN_AMOUNT / 2}"/></filter>`;
// A soft shadow cast by a mark's own silhouette; the mark itself passes through
// untouched. `size` is the mark's size in user units (the splash's raven is 300).
export const shadow = size => {
  const k = size / RAVEN.size;
  return '<filter id="shadow" x="-30%" y="-30%" width="160%" height="170%" color-interpolation-filters="sRGB">' +
    `<feGaussianBlur in="SourceAlpha" stdDeviation="${round(13 * k)}"/><feOffset dx="${round(5 * k)}" dy="${round(16 * k)}"/>` +
    '<feComponentTransfer><feFuncA type="linear" slope="0.5"/></feComponentTransfer>' +
    '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>';
};

// `artwork` is the approved raven as a data URI; `wordmark` is the faceted A and
// the "ngelo" outlines exactly as the wordmark files carry them. The grain is on
// the background only: the raven and the letters stay crisp.
export function splashSvg({artwork, wordmark}) {
  const {defs, layers} = facetedLight(SPLASH_SCENE);
  return '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
    `width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">` +
    `<defs>${defs}${grain(W, H)}${shadow(RAVEN.size)}</defs>` +
    `<g filter="url(#grain)">${layers}</g>` +
    `<image x="${RAVEN.x}" y="${RAVEN.y}" width="${RAVEN.size}" height="${RAVEN.size}" filter="url(#shadow)" xlink:href="${artwork}"/>` +
    `<g transform="${WORDMARK}">${wordmark}</g></svg>\n`;
}
