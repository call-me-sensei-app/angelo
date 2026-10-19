// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// Outlines for the wordmark's "ngelo": the bundled variable Inter at the approved
// weight 500 and -0.015 em letter spacing, as plain SVG paths for NanoSVG.
//
// Neither the canvas converter nor Blender can select a variable-font instance,
// so headless Chromium lays the text out (real shaping, kerning and spacing) and
// prints it to PDF; Skia writes the variable instance as Type3 glyphs whose path
// operators are copied here, with one deliberate substitution (``plainL``). Writes release/branding/angelo/
// angelo-wordmark-letters.svg, which angelo_branding.mjs consumes. The result is
// deterministic for a given font file and Chromium build; rerun only when the
// font, weight or spacing changes. From the repository root:
//   node build_files/utils/angelo_letters.mjs

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import {createRequire} from "node:module";
import {fileURLToPath} from "node:url";

export const LETTERS = {text: "ngelo", fontSize: 440, baseline: 380, weight: 500, opticalSize: 32, letterSpacing: -0.015};
export const OUTPUT = "release/branding/angelo/angelo-wordmark-letters.svg";
const FONT = "release/datafiles/fonts/Inter.woff2";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const round = value => String(Math.round(value * 1000) / 1000);

export const fontSha256 = () => crypto.createHash("sha256").update(fs.readFileSync(path.join(root, FONT))).digest("hex");

// The page used for both the outline extraction and visual comparisons.
export function lettersPage({width = 1600, height = 560, color = "#ffffff", background = "transparent"} = {}) {
  const font = fs.readFileSync(path.join(root, FONT)).toString("base64");
  const {text, fontSize, weight, opticalSize, letterSpacing} = LETTERS;
  // At line-height 1, Inter's 440 px line box puts the baseline at y=380 (checked in the PDF).
  return `<!doctype html><html><head><meta charset="utf-8"><style>
@font-face{font-family:"Angelo Inter";src:url("data:font/woff2;base64,${font}") format("woff2");font-weight:100 900}
@page{size:${width}px ${height}px;margin:0}
html,body{margin:0;width:${width}px;height:${height}px;background:${background};overflow:hidden}
#t{position:absolute;left:0;top:0;white-space:pre;color:${color};
  font:${weight} ${fontSize}px/1 "Angelo Inter";letter-spacing:${letterSpacing}em;
  font-variation-settings:"opsz" ${opticalSize};font-kerning:normal}
</style></head><body><span id="t">${text}</span></body></html>`;
}

export function loadPlaywright() {
  // Same deprecated fallbacks as angelo_branding.mjs.
  const candidates = [process.env.ANGELO_NODE_MODULES || process.env.SENSEI_NODE_MODULES || process.env.TOONLAB_NODE_MODULES,
    path.join(root, "node_modules"),
    path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")].filter(Boolean);
  for (const directory of candidates) {
    try {
      return createRequire(path.join(directory, "_angelo_loader.cjs"))("playwright");
    } catch { /* Try the next configured dependency directory. */ }
  }
  throw new Error("Set ANGELO_NODE_MODULES to a directory containing playwright (with its Chromium)");
}

// Minimal reader for Skia's PDF output: numbered objects, Flate streams, one page.
function readPdf(buffer) {
  const text = buffer.toString("latin1");
  const objects = new Map();
  for (const match of text.matchAll(/(\d+) 0 obj\s*([\s\S]*?)endobj/g)) {
    const body = match[2];
    const streamAt = body.indexOf("stream");
    if (streamAt < 0) {
      objects.set(Number(match[1]), {dict: body});
      continue;
    }
    const dict = body.slice(0, streamAt);
    let start = match.index + match[0].indexOf("stream") + 6;
    if (text[start] === "\r") start++;
    if (text[start] === "\n") start++;
    let data = buffer.subarray(start, start + Number(/\/Length (\d+)/.exec(dict)[1]));
    if (/\/FlateDecode/.test(dict)) data = zlib.inflateSync(data);
    objects.set(Number(match[1]), {dict, stream: data.toString("latin1")});
  }
  return objects;
}

const reference = (dict, key) => Number(new RegExp(`/${key} (\\d+) 0 R`).exec(dict)?.[1]);

function glyphPath(stream, scale) {
  const tokens = stream.trim().split(/\s+/);
  const commands = [], operands = [];
  const point = (x, y) => round(x * scale) + " " + round(y * scale);
  for (const token of tokens) {
    if (/^-?[\d.]+$/.test(token)) {
      operands.push(Number(token));
      continue;
    }
    const [a, b, c, d, e, f] = operands.splice(0);
    if (token === "d1" || token === "d0" || token === "f") continue;
    if (token === "m") commands.push("M" + point(a, b));
    else if (token === "l") commands.push("L" + point(a, b));
    else if (token === "c") commands.push("C" + point(a, b) + " " + point(c, d) + " " + point(e, f));
    else if (token === "h") commands.push("Z");
    else throw new Error(`Unexpected glyph operator ${token}; extend angelo_letters.mjs`);
  }
  return commands.join("");
}

// Pen positions and glyph outlines (CSS pixels, y down, baseline at LETTERS.baseline).
export function lettersFromPdf(buffer) {
  const objects = readPdf(buffer);
  const page = [...objects.values()].find(object => /\/Type \/Page\b/.test(object.dict));
  const content = objects.get(reference(page.dict, "Contents")).stream;
  const fonts = Object.fromEntries([.../\/Font <<([^>]*)>>/.exec(page.dict)[1].matchAll(/\/(\w+) (\d+) 0 R/g)]
    .map(([, name, number]) => [name, objects.get(Number(number)).dict]));
  const block = /BT([\s\S]*?)ET/.exec(content)[1];
  if (!/1 0 0 -1 0 ([\d.]+) Tm/.test(block) || Number(/1 0 0 -1 0 ([\d.]+) Tm/.exec(block)[1]) !== LETTERS.baseline) {
    throw new Error("Unexpected text matrix; the page layout no longer puts the baseline at " + LETTERS.baseline);
  }
  const glyphs = [];
  let font = null, size = 0, pen = 0;
  for (const [, operation] of block.matchAll(/(\/\w+ [\d.]+ Tf|-?[\d.]+ -?[\d.]+ Td|<[0-9A-Fa-f]+> Tj)/g)) {
    if (operation.endsWith("Tf")) {
      [font, size] = [operation.split(" ")[0].slice(1), Number(operation.split(" ")[1])];
    } else if (operation.endsWith("Td")) {
      const [dx, dy] = operation.split(" ").map(Number);
      if (dy !== 0) throw new Error("The letters wrapped onto another line");
      pen += dx;  // Td moves the line origin: offsets accumulate from the line start.
    } else {
      const dict = fonts[font];
      const matrix = /\/FontMatrix \[([^\]]*)\]/.exec(dict)[1].trim().split(/\s+/).map(Number);
      const names = /\/Differences \[([^\]]*)\]/.exec(dict)[1].trim().split(/\s+/).slice(1).map(name => name.slice(1));
      const charProcs = /\/CharProcs <<([^>]*)>>/.exec(dict)[1];
      const codes = operation.slice(1, operation.indexOf(">")).match(/../g).map(hex => parseInt(hex, 16));
      if (codes.length !== 1) throw new Error("Expected one glyph per positioned run");
      const name = names[codes[0]];
      const procedure = objects.get(Number(new RegExp(`/${name} (\\d+) 0 R`).exec(charProcs)[1])).stream;
      if (Math.abs(matrix[0] + matrix[3]) > 1e-12) throw new Error("Unexpected Type3 font matrix");
      glyphs.push({x: pen, d: glyphPath(procedure, matrix[0] * size)});
    }
  }
  if (glyphs.length !== LETTERS.text.length) throw new Error(`Expected ${LETTERS.text.length} glyphs, found ${glyphs.length}`);
  return glyphs;
}

// Deliberate glyph substitution. The approved source was rendered with web Inter,
// whose default "l" is a plain vertical stem; Blender's bundled Inter only has a
// tailed "l". Keep the weight-500 stem's left edge, width and ascender, end it on
// the baseline without a tail, and give it a right side bearing equal to its left
// one. The following letters move by the resulting change of advance; the
// -0.015 em tracking still applies after the "l" (any "l" kerning is dropped).
export function plainL(glyphs) {
  const tracking = LETTERS.letterSpacing * LETTERS.fontSize;
  return glyphs.reduce((result, glyph, index) => {
    const shift = result.shift;
    if (LETTERS.text[index] !== "l") {
      result.glyphs.push({...glyph, x: glyph.x + shift});
      return result;
    }
    // On-curve points only: the top edge of the stem is the outline's highest segment.
    const points = [...glyph.d.matchAll(/([MLC])([^MLCZ]*)/g)].map(([, command, values]) =>
      values.trim().split(/\s+/).map(Number).slice(command === "C" ? 4 : 0));
    const top = Math.min(...points.map(([, y]) => y));
    const topXs = points.filter(([, y]) => y === top).map(([x]) => x);
    const [left, right] = [Math.min(...topXs), Math.max(...topXs)];
    if (left !== Math.min(...points.map(([x]) => x)) || right <= left) {
      throw new Error("The \"l\" no longer has a stem at its left edge; review plainL");
    }
    const next = glyphs[index + 1];
    const advance = left + (right - left) + left;  // Right side bearing = left side bearing.
    const d = `M${round(left)} ${round(top)}L${round(right)} ${round(top)}L${round(right)} 0L${round(left)} 0Z`;
    result.glyphs.push({...glyph, x: glyph.x + shift, d});
    result.shift = next ? glyph.x + advance + tracking - next.x + shift : shift;
    return result;
  }, {glyphs: [], shift: 0}).glyphs;
}

export async function extractLetters() {
  const {chromium} = loadPlaywright();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.setContent(lettersPage());
    await page.evaluate(() => document.fonts.ready);
    const pdf = await page.pdf({width: "1600px", height: "560px", printBackground: false});
    const instance = /\/FontName \/\w+\+(\w+)/.exec(pdf.toString("latin1"))?.[1] || "";
    const expected = `wght${(LETTERS.weight * 65536).toString(16).toUpperCase()}`;
    if (!instance.includes(expected)) throw new Error(`Chromium did not use weight ${LETTERS.weight}: ${instance}`);
    return {glyphs: plainL(lettersFromPdf(pdf)), instance, chromium: browser.version()};
  } finally {
    await browser.close();
  }
}

export function lettersSvg({glyphs, instance}) {
  const {text, fontSize, baseline, weight, opticalSize, letterSpacing} = LETTERS;
  return "<!-- SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.\n     SPDX-License-Identifier: GPL-2.0-or-later\n" +
    `     Generated by build_files/utils/angelo_letters.mjs; do not edit. "${text}" in ${FONT}\n` +
    `     at weight ${weight}, optical size ${opticalSize}, letter spacing ${letterSpacing} em, ${fontSize} px,\n` +
    `     baseline y=${baseline} (variable instance ${instance}); the "l" is the plain stem from plainL. -->\n` +
    `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="560" viewBox="0 0 1600 560" data-font-sha256="${fontSha256()}">\n` +
    glyphs.map(glyph => `<path fill="#ffffff" transform="translate(${round(glyph.x)} ${baseline})" d="${glyph.d}"/>\n`).join("") +
    "</svg>\n";
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const letters = await extractLetters();
  fs.writeFileSync(path.join(root, OUTPUT), lettersSvg(letters));
  console.log(`Wrote ${OUTPUT}: ${letters.instance}, Chromium ${letters.chromium}`);
}
