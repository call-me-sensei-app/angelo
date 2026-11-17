// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Before a live frame is parsed: an image the page already holds (same `angelo_digest`) is pointed at a 1 x 1
// stub, exactly as Angelo marks one (`angelo_stub`), so the loader never decodes a picture the page has. A full
// frame carries every image; without this, each one would cost a decode on every frame even when nothing changed.

const STUB = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1, 8, 6,
  0, 0, 0, 31, 21, 196, 137, 0, 0, 0, 11, 73, 68, 65, 84, 120, 218, 99, 96, 0, 2, 0, 0, 5, 0, 1, 233, 250, 220, 216, 0, 0,
  0, 0, 73, 69, 78, 68, 174, 66, 96, 130]); // A valid transparent 1 x 1 RGBA PNG.

const JSON_CHUNK = 0x4e4f534a;
const BIN_CHUNK = 0x004e4942;

/**
 * `bytes` (an ArrayBuffer of a .glb) with every image whose digest `known(digest)` accepts turned into a stub.
 * Returns the input unchanged when there is nothing to do or it isn't a .glb this can read.
 */
export function stubKnownImages(bytes, known) {
  const view = new DataView(bytes);
  if (bytes.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67 || view.getUint32(16, true) !== JSON_CHUNK) {
    return bytes;
  }
  const jsonLength = view.getUint32(12, true);
  let json;
  try {
    json = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, 20, jsonLength)));
  } catch {
    return bytes;
  }
  const images = (json.images ?? []).filter((image) => image.bufferView !== undefined && image.extras?.angelo_stub !== true
    && typeof image.extras?.angelo_digest === "string" && known(image.extras.angelo_digest));
  const binAt = 20 + jsonLength;
  if (!images.length || binAt + 8 > bytes.byteLength || view.getUint32(binAt + 4, true) !== BIN_CHUNK) return bytes;
  const binLength = view.getUint32(binAt, true);
  const stubAt = Math.ceil(binLength / 4) * 4;
  for (const image of images) {
    // One view per image, all over the same bytes: GLTFLoader caches decoded images by bufferView, so a shared view
    // would make every stub one texture.
    json.bufferViews.push({ buffer: 0, byteOffset: stubAt, byteLength: STUB.length });
    image.bufferView = json.bufferViews.length - 1;
    image.mimeType = "image/png";
    image.extras.angelo_stub = true;
  }
  json.buffers[0].byteLength = stubAt + STUB.length;
  let text = new TextEncoder().encode(JSON.stringify(json));
  const padded = Math.ceil(text.length / 4) * 4;
  const newBin = Math.ceil((stubAt + STUB.length) / 4) * 4;
  const out = new Uint8Array(12 + 8 + padded + 8 + newBin);
  const outView = new DataView(out.buffer);
  outView.setUint32(0, 0x46546c67, true);
  outView.setUint32(4, 2, true);
  outView.setUint32(8, out.length, true);
  outView.setUint32(12, padded, true);
  outView.setUint32(16, JSON_CHUNK, true);
  out.set(text, 20);
  out.fill(0x20, 20 + text.length, 20 + padded);
  text = null;
  const at = 20 + padded;
  outView.setUint32(at, newBin, true);
  outView.setUint32(at + 4, BIN_CHUNK, true);
  out.set(new Uint8Array(bytes, binAt + 8, binLength), at + 8);
  out.set(STUB, at + 8 + stubAt);
  return out.buffer;
}
