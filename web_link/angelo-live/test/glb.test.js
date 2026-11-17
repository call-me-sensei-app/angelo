// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Pictures the page holds are turned into stubs before a live frame is parsed (src/browser/glb.js).

import { describe, expect, it } from "vitest";
import { stubKnownImages } from "../src/browser/glb.js";
import { fullGlb, imageDigests } from "./fixtures/gltf.js";
import { readGlb } from "./fixtures/glb-edit.js";

const spec = { textures: { a: { size: 32, seed: 1 }, b: { size: 32, seed: 2 } },
  materials: { ma: { texture: "a" }, mb: { texture: "b" } },
  nodes: [{ name: "A", uid: "1", mesh: { shape: "box", material: "ma" } }, { name: "B", uid: "2", mesh: { shape: "box", material: "mb" } }] };

const arrayBuffer = (buffer) => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);

describe("stubbing known pictures", () => {
  it("points each known image at its own stub view and keeps every other byte", () => {
    const glb = fullGlb(spec);
    const known = imageDigests(spec);
    const out = Buffer.from(stubKnownImages(arrayBuffer(glb), (digest) => known.has(digest)));
    const before = readGlb(glb);
    const after = readGlb(out);
    const views = after.json.images.map((image) => image.bufferView);
    expect(new Set(views).size).toBe(2); // GLTFLoader caches pictures by view: one each.
    expect(after.json.images.every((image) => image.extras.angelo_stub === true)).toBe(true);
    for (const view of views) expect(after.json.bufferViews[view].byteLength).toBe(68); // The 1 x 1 PNG.
    const original = before.json.bufferViews.length;
    expect(after.bin.subarray(0, before.bin.length).equals(before.bin)).toBe(true);
    expect(after.json.bufferViews.slice(0, original)).toEqual(before.json.bufferViews);
  });

  it("returns the frame as it is when nothing is known", () => {
    const glb = arrayBuffer(fullGlb(spec));
    expect(stubKnownImages(glb, () => false)).toBe(glb);
  });
});
