// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The Angelo wordmark's faceted "A" (approved "Option B"): a triangular letter
// with an even-odd counter, faceted in the raven mark's teal palette as a nod to
// low-poly meshes. The remaining letters ("ngelo") are outlined Inter text, set
// by angelo_branding.mjs exactly like the previous wordmark.
//
// Units are the approved source's 100 x 88 box (origin top-left, y down). NanoSVG,
// which renders the About wordmark, has no clip-path, so the facets are clipped
// to the letter here and written as plain polygons. Pure geometry: no dependencies.

const TRIANGLE = [[50, 0], [100, 88], [0, 88]];
// Removed from the triangle: the gap between the legs and the counter.
const NOTCH = [[17, 88], [30.6, 64], [69.4, 64], [83, 88]];
const COUNTER = [[50, 30], [62.5, 52], [37.5, 52]];
export const A_WIDTH = 100;
export const A_HEIGHT = 88;
export const A_OUTLINE = "M50 0L100 88L83 88L69.4 64L30.6 64L17 88L0 88Z M50 30L62.5 52L37.5 52Z";

// The approved source painted the lower facets over the upper ones; these are the
// visible, non-overlapping regions. `mono` is the opacity of the same facet in the
// single-colour variant, ordered like the teal values (light from the upper left).
export const FACETS = [
  {name: "apex", color: "#a1f4d6", mono: 1.00, points: [[50, 0], [31, 33], [69, 33]]},
  {name: "left", color: "#27bdbf", mono: 0.86, points: [[31, 33], [50, 33], [50, 58], [0, 88]]},
  {name: "left-foot", color: "#1aa3ad", mono: 0.74, points: [[0, 88], [50, 58], [50, 88]]},
  {name: "right", color: "#0b8a99", mono: 0.66, points: [[50, 33], [69, 33], [100, 88], [50, 58]]},
  {name: "right-foot", color: "#087787", mono: 0.56, points: [[50, 58], [100, 88], [50, 88]]},
];
// A full-letter layer under the facets hides anti-aliasing seams between them.
const BASE_COLOR = "#1aa3ad";
const BASE_MONO = 0.5;

const cross = (a, b, p) => (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
const area = polygon => polygon.reduce((sum, p, i) => {
  const q = polygon[(i + 1) % polygon.length];
  return sum + p[0] * q[1] - q[0] * p[1];
}, 0) / 2;
const positive = polygon => (area(polygon) < 0 ? [...polygon].reverse() : polygon);

// The part of `polygon` on the left (inside) or right of the directed line a -> b.
function halfPlane(polygon, a, b, inside) {
  const side = p => (inside ? 1 : -1) * cross(a, b, p);
  const result = [];
  polygon.forEach((p, i) => {
    const q = polygon[(i + 1) % polygon.length];
    const sp = side(p), sq = side(q);
    if (sp >= 0) result.push(p);
    if ((sp > 0 && sq < 0) || (sp < 0 && sq > 0)) {
      const t = sp / (sp - sq);
      result.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]);
    }
  });
  return result;
}

// Intersection with a convex polygon (Sutherland-Hodgman).
function clip(polygon, convex) {
  convex = positive(convex);
  return convex.reduce((piece, a, i) => (piece.length ? halfPlane(piece, a, convex[(i + 1) % convex.length], true) : piece),
                       positive(polygon));
}

// Disjoint convex pieces of convex `polygon` outside convex `hole`.
function subtract(polygon, hole) {
  hole = positive(hole);
  const pieces = [];
  let rest = positive(polygon);
  hole.forEach((a, i) => {
    if (!rest.length) return;
    const b = hole[(i + 1) % hole.length];
    pieces.push(halfPlane(rest, a, b, false));
    rest = halfPlane(rest, a, b, true);
  });
  return pieces.filter(piece => piece.length >= 3 && Math.abs(area(piece)) > 1e-6);
}

const number = value => String(Math.round(value * 1000) / 1000);

// Each facet clipped to the letter, as one path of disjoint convex pieces.
export function facetPieces(facet) {
  return subtract(clip(facet.points, TRIANGLE), NOTCH).flatMap(piece => subtract(piece, COUNTER));
}

export function facetPath(facet) {
  return facetPieces(facet).map(piece => "M" + piece.map(([x, y]) => number(x) + " " + number(y)).join("L") + "Z").join("");
}

// SVG content for the letter in its 100 x 88 box. `mono` gives a single-colour
// variant whose facets keep their light-to-dark order through opacity.
export function facetedA({mono = null} = {}) {
  const base = mono
    ? `<path fill="${mono}" fill-opacity="${BASE_MONO}" fill-rule="evenodd" d="${A_OUTLINE}"/>`
    : `<path fill="${BASE_COLOR}" fill-rule="evenodd" d="${A_OUTLINE}"/>`;
  return base + FACETS.map(facet => {
    if (!mono) return `<path fill="${facet.color}" d="${facetPath(facet)}"/>`;
    // Painted over the base layer: solve for the facet opacity that composites to `facet.mono`.
    const opacity = number(1 - (1 - facet.mono) / (1 - BASE_MONO));
    return `<path fill="${mono}" fill-opacity="${opacity}" d="${facetPath(facet)}"/>`;
  }).join("");
}

export const testing = {area, clip, subtract, TRIANGLE, NOTCH, COUNTER};
