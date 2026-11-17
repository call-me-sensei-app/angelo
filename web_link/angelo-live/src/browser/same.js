// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// "Is this the same as what the page already shows?" A new parse makes new geometry and material objects even for
// what didn't change; keeping the page's own objects when the content is equal means no GPU upload, no shader
// compile and no event for an unchanged node. Comparisons are exact (bytes and values), never hashes.

import { textureKey } from "./textures.js";

function bytesOf(array) {
  const aligned = array.byteOffset % 4 === 0 && array.byteLength % 4 === 0;
  return aligned ? new Uint32Array(array.buffer, array.byteOffset, array.byteLength / 4)
    : new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
}

export function sameArray(a, b) {
  if (a === b) return true;
  if (!a || !b || a.constructor !== b.constructor || a.length !== b.length) return false;
  const x = bytesOf(a);
  const y = bytesOf(b);
  if (x.length !== y.length || x.constructor !== y.constructor) return false;
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return false;
  return true;
}

function sameAttribute(a, b) {
  if (a === b) return true;
  if (!a || !b || a.itemSize !== b.itemSize || a.normalized !== b.normalized || a.count !== b.count) return false;
  if (a.isInterleavedBufferAttribute || b.isInterleavedBufferAttribute) {
    if (!a.isInterleavedBufferAttribute || !b.isInterleavedBufferAttribute) return false;
    if (a.offset !== b.offset || a.data.stride !== b.data.stride) return false;
    return sameArray(a.data.array, b.data.array);
  }
  return sameArray(a.array, b.array);
}

function sameKeys(a, b) {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => key in b);
}

/** Same vertex data, index, groups, morph targets and draw range. */
export function sameGeometry(a, b) {
  if (a === b) return true;
  if (!a || !b || !sameKeys(a.attributes, b.attributes) || !sameKeys(a.morphAttributes, b.morphAttributes)) return false;
  if (a.morphTargetsRelative !== b.morphTargetsRelative) return false;
  if (a.drawRange.start !== b.drawRange.start || a.drawRange.count !== b.drawRange.count) return false;
  if (a.groups.length !== b.groups.length || a.groups.some((group, i) => group.start !== b.groups[i].start
    || group.count !== b.groups[i].count || group.materialIndex !== b.groups[i].materialIndex)) return false;
  if (Boolean(a.index) !== Boolean(b.index) || (a.index && !sameAttribute(a.index, b.index))) return false;
  for (const name of Object.keys(a.attributes)) if (!sameAttribute(a.attributes[name], b.attributes[name])) return false;
  for (const name of Object.keys(a.morphAttributes)) {
    const x = a.morphAttributes[name];
    const y = b.morphAttributes[name];
    if (x.length !== y.length || x.some((item, i) => !sameAttribute(item, y[i]))) return false;
  }
  return true;
}

// Fields every material has that say nothing about how it draws, or that three changes by itself.
const IGNORED = new Set(["uuid", "id", "version", "userData", "name", "needsUpdate", "isMaterial", "type"]);

function sameValue(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") {
    return typeof a === "number" && typeof b === "number" && Number.isNaN(a) && Number.isNaN(b);
  }
  if (a.isTexture || b.isTexture) {
    const x = a.isTexture ? textureKey(a) : null;
    return Boolean(x) && x === (b.isTexture ? textureKey(b) : null);
  }
  if (typeof a.equals === "function" && a.constructor === b.constructor) return a.equals(b);
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, i) => sameValue(item, b[i]));
  if (a.constructor === Object && b.constructor === Object) {
    return sameKeys(a, b) && Object.keys(a).every((key) => sameValue(a[key], b[key]));
  }
  return false;
}

/** Same kind of material with the same values; textures are the same when their images and samplers are. */
export function sameMaterial(a, b) {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, i) => sameMaterial(item, b[i]));
  }
  if (!a || !b || a.constructor !== b.constructor || a.type !== b.type) return false;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (IGNORED.has(key) || key.startsWith("_")) continue;
    const x = a[key];
    const y = b[key];
    if (typeof x === "function" || typeof y === "function") continue;
    if (!sameValue(x, y)) return false;
  }
  return true;
}

/** Same local transform (exactly: Angelo writes the same numbers for what didn't move). */
export function sameTransform(a, b) {
  return a.position.equals(b.position) && a.quaternion.equals(b.quaternion) && a.scale.equals(b.scale);
}
