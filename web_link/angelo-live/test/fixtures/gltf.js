// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Test .glb files made in code, so nothing large is downloaded or committed. A scene is described as a tree:
//
//   sceneGlb({ nodes: [{ name: "Body", uid: "7", mesh: { shape: "box", material: "paint" },
//                        children: [{ name: "Wheel", uid: "8", mesh: { shape: "sphere", segments: 12 } }] }],
//              materials: { paint: { color: [1, 0, 0, 1], texture: "noise" } },
//              textures: { noise: { size: 256 } } }, { uids: true })
//
// `uids: false` leaves extras.angelo_uid out, as in a saved file. A node may have `primitives` (several meshes),
// `skin` ({bones, segments, rings, length}: a skinned tube with a bone chain), `light` or `camera`.

import { createHash } from "node:crypto";
import zlib from "node:zlib";

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
// The 1 x 1 PNG sent for an image every receiving page already has (PROTOCOL.md §1.2): transparent RGBA, valid
// (its IDAT inflates to the filter byte plus four channel bytes).
export const STUB_PNG = Buffer.from("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
  + "0000000b4944415478da636000020000050001e9fadcd80000000049454e44ae426082", "hex");

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const out = Buffer.alloc(12 + data.length);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, "latin1");
  data.copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
  return out;
}

/** An RGBA PNG of `width` x `height`; `pixel(x, y)` returns [r, g, b, a] bytes. */
export function png(width, height, pixel) {
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    const row = y * (width * 4 + 1);
    for (let x = 0; x < width; x++) {
      const [r, g, b, a] = pixel(x, y);
      raw.set([r, g, b, a], row + 1 + x * 4);
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 6, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw, { level: 6 })), chunk("IEND", Buffer.alloc(0))]);
}

const noiseCache = new Map();

/**
 * A texture-like picture: gradients plus deterministic noise, so it compresses the way a real one does. Made once
 * per (size, seed): a 2K one takes a while, and a test asks for the same picture in every frame it writes.
 */
export function noisePng(size, seed = 1) {
  const key = `${size}:${seed}`;
  if (noiseCache.has(key)) return noiseCache.get(key);
  let state = seed >>> 0 || 1;
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    let at = y * (size * 4 + 1) + 1;
    for (let x = 0; x < size; x++) {
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      raw[at++] = (x * 255 / size) | 0;
      raw[at++] = (y * 255 / size) | 0;
      raw[at++] = (state >>> 24) & 255;
      raw[at++] = 255;
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header.set([8, 6, 0, 0, 0], 8);
  const made = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw, { level: 6 })), chunk("IEND", Buffer.alloc(0))]);
  noiseCache.set(key, made);
  return made;
}

// Geometry ---------------------------------------------------------------------------------------------------------

export function box(size = 1) {
  const h = size / 2;
  const faces = [[0, 1, 2, 1], [0, 1, 2, -1], [1, 2, 0, 1], [1, 2, 0, -1], [2, 0, 1, 1], [2, 0, 1, -1]];
  const positions = [];
  const normals = [];
  const indices = [];
  for (const [u, v, w, sign] of faces) {
    const base = positions.length / 3;
    for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const p = [0, 0, 0];
      p[u] = a * h;
      p[v] = b * h * sign;
      p[w] = sign * h;
      positions.push(...p);
      const n = [0, 0, 0];
      n[w] = sign;
      normals.push(...n);
    }
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  const uvs = Array.from({ length: 24 }, (_, i) => [[0, 0], [1, 0], [1, 1], [0, 1]][i % 4]).flat();
  return { positions: new Float32Array(positions), normals: new Float32Array(normals), uvs: new Float32Array(uvs),
    indices: new Uint32Array(indices) };
}

/** A UV sphere: 2 * segments * (rings - 1) triangles. */
export function sphere(radius = 0.5, segments = 16, rings = 12) {
  const positions = [];
  const normals = [];
  const uvs = [];
  const indices = [];
  for (let r = 0; r <= rings; r++) {
    const phi = (r / rings) * Math.PI;
    for (let s = 0; s <= segments; s++) {
      const theta = (s / segments) * Math.PI * 2;
      const n = [Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta)];
      normals.push(...n);
      positions.push(n[0] * radius, n[1] * radius, n[2] * radius);
      uvs.push(s / segments, r / rings);
    }
  }
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * (segments + 1) + s;
      const b = a + segments + 1;
      if (r !== 0) indices.push(a, b, a + 1);
      if (r !== rings - 1) indices.push(a + 1, b, b + 1);
    }
  }
  return { positions: new Float32Array(positions), normals: new Float32Array(normals), uvs: new Float32Array(uvs),
    indices: new Uint32Array(indices) };
}

/** A tube along +Y skinned to `bones` joints: 2 * segments * rings triangles. */
export function skinnedTube({ bones = 4, segments = 16, rings = 16, length = 2, radius = 0.2 }) {
  const positions = [];
  const normals = [];
  const joints = [];
  const weights = [];
  const indices = [];
  for (let r = 0; r <= rings; r++) {
    const y = (r / rings) * length;
    const along = (r / rings) * (bones - 1);
    const j0 = Math.min(Math.floor(along), bones - 1);
    const j1 = Math.min(j0 + 1, bones - 1);
    const t = along - j0;
    for (let s = 0; s <= segments; s++) {
      const theta = (s / segments) * Math.PI * 2;
      positions.push(Math.cos(theta) * radius, y, Math.sin(theta) * radius);
      normals.push(Math.cos(theta), 0, Math.sin(theta));
      joints.push(j0, j1, 0, 0);
      weights.push(1 - t, t, 0, 0);
    }
  }
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * (segments + 1) + s;
      const b = a + segments + 1;
      indices.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  return { positions: new Float32Array(positions), normals: new Float32Array(normals),
    joints: new Uint16Array(joints), weights: new Float32Array(weights), indices: new Uint32Array(indices) };
}

function shapeOf(mesh) {
  if (mesh.shape === "sphere") return sphere(mesh.radius ?? 0.5, mesh.segments ?? 16, mesh.rings ?? 12);
  return box(mesh.size ?? 1);
}

// The writer -------------------------------------------------------------------------------------------------------

export class Writer {
  constructor() {
    this.json = { asset: { version: "2.0", generator: "angelo-live test fixtures" }, scene: 0, scenes: [{ nodes: [] }],
      nodes: [], meshes: [], materials: [], buffers: [{ byteLength: 0 }], bufferViews: [], accessors: [] };
    this.parts = [];
    this.length = 0;
  }

  view(bytes, target) {
    const pad = (4 - (this.length % 4)) % 4;
    if (pad) {
      this.parts.push(Buffer.alloc(pad));
      this.length += pad;
    }
    const view = { buffer: 0, byteOffset: this.length, byteLength: bytes.byteLength, ...(target ? { target } : {}) };
    this.parts.push(Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength));
    this.length += bytes.byteLength;
    this.json.bufferViews.push(view);
    return this.json.bufferViews.length - 1;
  }

  accessor(array, type, componentType, target, extra = {}) {
    const size = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }[type];
    const accessor = { bufferView: this.view(array, target), componentType, count: array.length / size, type, ...extra };
    if (type === "VEC3" && componentType === 5126 && !extra.noBounds) {
      const min = [Infinity, Infinity, Infinity];
      const max = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < array.length; i += 3) {
        for (let k = 0; k < 3; k++) {
          min[k] = Math.min(min[k], array[i + k]);
          max[k] = Math.max(max[k], array[i + k]);
        }
      }
      Object.assign(accessor, { min, max });
    }
    delete accessor.noBounds;
    this.json.accessors.push(accessor);
    return this.json.accessors.length - 1;
  }

  primitive(geometry, material) {
    const attributes = { POSITION: this.accessor(geometry.positions, "VEC3", 5126, 34962) };
    if (geometry.normals) attributes.NORMAL = this.accessor(geometry.normals, "VEC3", 5126, 34962, { noBounds: true });
    if (geometry.uvs) attributes.TEXCOORD_0 = this.accessor(geometry.uvs, "VEC2", 5126, 34962);
    if (geometry.joints) attributes.JOINTS_0 = this.accessor(geometry.joints, "VEC4", 5123, 34962);
    if (geometry.weights) attributes.WEIGHTS_0 = this.accessor(geometry.weights, "VEC4", 5126, 34962);
    const primitive = { attributes, indices: this.accessor(geometry.indices, "SCALAR", 5125, 34963) };
    if (material !== undefined) primitive.material = material;
    return primitive;
  }

  glb() {
    this.json.buffers[0].byteLength = this.length;
    if (!this.json.materials.length) delete this.json.materials;
    for (const key of ["images", "textures", "samplers", "skins"]) if (this.json[key] && !this.json[key].length) delete this.json[key];
    const json = Buffer.from(JSON.stringify(this.json), "utf8");
    const jsonChunk = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
    const bin = Buffer.concat(this.parts);
    const binChunk = Buffer.concat([bin, Buffer.alloc((4 - (bin.length % 4)) % 4)]);
    const header = Buffer.alloc(12);
    header.writeUInt32LE(0x46546c67, 0);
    header.writeUInt32LE(2, 4);
    header.writeUInt32LE(12 + 8 + jsonChunk.length + 8 + binChunk.length, 8);
    const head = (length, type) => {
      const out = Buffer.alloc(8);
      out.writeUInt32LE(length, 0);
      out.writeUInt32LE(type, 4);
      return out;
    };
    return Buffer.concat([header, head(jsonChunk.length, 0x4e4f534a), jsonChunk, head(binChunk.length, 0x004e4942),
      binChunk]);
  }
}

/**
 * A .glb Buffer of the scene `spec` (see the file comment). `live` writes it as Angelo's live frames are written
 * (PROTOCOL.md §1.2): every node's role and every content node's digest, images by digest, and an image whose
 * digest is in `knownImages` as a stub. `content` (a Set of uids) makes the other nodes `parent` locators: empties.
 */
export function sceneGlb(spec, { uids = true, live = false, content = null, knownImages = null } = {}) {
  const pngOf = (name) => {
    const def = spec.textures?.[name] ?? { size: 64 };
    return def.png ?? noisePng(def.size ?? 64, def.seed ?? 1);
  };
  const materialFacts = (name) => {
    const def = spec.materials?.[name] ?? {};
    return { name, ...def, texture: def.texture ? sha256(pngOf(def.texture)) : undefined };
  };
  const digestOf = (node, extra = {}) => sha256(JSON.stringify({ name: node.name, position: node.position,
    rotation: node.rotation, scale: node.scale, light: node.light, skin: node.skin, ...extra,
    primitives: (node.primitives ?? (node.mesh ? [node.mesh] : [])).map((mesh) => ({ ...mesh, geometry: undefined,
      material: materialFacts(mesh.material) })),
    children: node.digestChildren ?? (node.children ?? []).map((child) => String(child.uid)) }));
  const roleOf = (uid) => (content === null || content.has(String(uid)) ? "content" : "parent");
  const writer = new Writer();
  const { json } = writer;
  const materialIndex = new Map();
  const textureIndex = new Map();
  const texture = (name) => {
    if (!textureIndex.has(name)) {
      const def = spec.textures?.[name] ?? { size: 64 };
      json.images ??= [];
      json.textures ??= [];
      json.samplers ??= [{ magFilter: 9729, minFilter: 9987 }];
      const bytes = pngOf(name);
      const image = { bufferView: null, mimeType: "image/png" };
      if (live) {
        const digest = sha256(bytes);
        image.extras = { angelo_digest: digest };
        if (knownImages?.has(digest)) image.extras.angelo_stub = true;
      }
      image.bufferView = writer.view(image.extras?.angelo_stub ? STUB_PNG : bytes);
      json.images.push(image);
      json.textures.push({ source: json.images.length - 1, sampler: 0 });
      textureIndex.set(name, json.textures.length - 1);
    }
    return textureIndex.get(name);
  };
  const material = (name) => {
    if (name === undefined) return undefined;
    if (!materialIndex.has(name)) {
      const def = spec.materials?.[name] ?? {};
      const pbr = { baseColorFactor: def.color ?? [0.8, 0.8, 0.8, 1], metallicFactor: 0, roughnessFactor: 0.6 };
      if (def.texture) pbr.baseColorTexture = { index: texture(def.texture) };
      json.materials.push({ name, pbrMetallicRoughness: pbr });
      materialIndex.set(name, json.materials.length - 1);
    }
    return materialIndex.get(name);
  };
  const addNode = (node, parentIndex) => {
    const index = json.nodes.length;
    const def = { name: node.name };
    json.nodes.push(def);
    if (uids && node.uid !== undefined) def.extras = { angelo_uid: String(node.uid) };
    const role = live ? roleOf(node.uid) : null;
    if (live) {
      def.extras.angelo_role = role;
      if (role === "content") def.extras.angelo_digest = node.digest ?? digestOf(node);
    }
    if (node.extras) def.extras = { ...def.extras, ...node.extras };
    if (node.position) def.translation = node.position;
    if (node.rotation) def.rotation = node.rotation;
    if (node.scale) def.scale = node.scale;
    if (parentIndex === null) json.scenes[0].nodes.push(index);
    else (json.nodes[parentIndex].children ??= []).push(index);
    const primitives = role === "parent" ? [] : node.primitives ?? (node.mesh ? [node.mesh] : []);
    if (primitives.length) {
      json.meshes.push({ name: node.meshName ?? node.name,
        primitives: primitives.map((mesh) => writer.primitive(mesh.geometry ?? shapeOf(mesh), material(mesh.material))) });
      def.mesh = json.meshes.length - 1;
    }
    if (node.light && role !== "parent") {
      json.extensionsUsed = ["KHR_lights_punctual"];
      const lights = ((json.extensions ??= {}).KHR_lights_punctual ??= { lights: [] }).lights;
      lights.push({ type: node.light.type ?? "point", color: node.light.color ?? [1, 1, 1],
        intensity: node.light.intensity ?? 1 });
      def.extensions = { KHR_lights_punctual: { light: lights.length - 1 } };
    }
    for (const child of node.children ?? []) addNode(child, index);
    if (node.skin) addSkin(node, index);
    return index;
  };
  const addSkin = (node, index) => {
    // The node is the armature; it gets a bone chain and a skinned mesh child.
    const { bones = 4, length = 2 } = node.skin;
    const boneIndices = [];
    let parent = index;
    for (let i = 0; i < bones; i++) {
      const boneName = node.skin.names?.[i] ?? `Bone_${i}`;
      const bone = { name: boneName, uid: `${node.uid}/${boneName}`, position: [0, i === 0 ? 0 : length / (bones - 1), 0] };
      bone.digest = digestOf(bone, { bone: true });
      parent = addNode(bone, parent);
      boneIndices.push(parent);
    }
    const inverse = new Float32Array(16 * bones);
    for (let i = 0; i < bones; i++) {
      inverse.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -(i * length) / (bones - 1), 0, 1], i * 16);
    }
    json.skins ??= [];
    json.skins.push({ joints: boneIndices, inverseBindMatrices: writer.accessor(inverse, "MAT4", 5126) });
    const meshNode = { name: node.skin.meshName ?? `${node.name}_Mesh`, uid: node.skin.meshUid ?? `${node.uid}m`,
      mesh: { geometry: skinnedTube(node.skin), material: node.skin.material } };
    meshNode.digest = digestOf(meshNode, { skin: node.skin, mesh: "skinned" });
    if (live && roleOf(meshNode.uid) === "parent") return; // A delta that doesn't change the skinned mesh.
    const meshIndex = addNode(meshNode, index);
    json.nodes[meshIndex].skin = json.skins.length - 1;
  };
  for (const node of spec.nodes) addNode(node, null);
  return writer.glb();
}

/** A deep copy of a spec, to edit for the next frame. */
export function cloneSpec(spec) {
  return JSON.parse(JSON.stringify(spec, (key, value) => (ArrayBuffer.isView(value) ? Array.from(value) : value)));
}

/** Find a node in a spec by name (depth first). */
export function findNode(nodes, name) {
  for (const node of nodes) {
    if (node.name === name) return node;
    const found = findNode(node.children ?? [], name);
    if (found) return found;
  }
  return null;
}

/** Remove a node from a spec by name; returns it. */
export function removeNode(nodes, name) {
  const index = nodes.findIndex((node) => node.name === name);
  if (index >= 0) return nodes.splice(index, 1)[0];
  for (const node of nodes) {
    const found = removeNode(node.children ?? [], name);
    if (found) return found;
  }
  return null;
}

/**
 * A delta .glb as Angelo writes one: the nodes whose uid is in `changed` as `content`, under their ancestors as
 * `parent` locators (empties); an armature whose skinned mesh changed comes with its bones as parents. Images in
 * `knownImages` (digests) are stubs.
 */
export function deltaGlb(spec, changed, { knownImages = null } = {}) {
  const content = new Set(changed.map(String));
  const keep = (node) => {
    const skinned = node.skin && (content.has(node.skin.meshUid ?? `${node.uid}m`)
      || Array.from({ length: node.skin.bones ?? 4 }, (_, i) => `${node.uid}/Bone_${i}`).some((uid) => content.has(uid)));
    const children = (node.children ?? []).map(keep).filter(Boolean);
    if (!content.has(String(node.uid)) && !children.length && !skinned) return null;
    // A content node's digest covers all its children's keys, as in a full frame, not just those sent along.
    return { ...node, children, skin: node.skin, digestChildren: (node.children ?? []).map((child) => String(child.uid)) };
  };
  const nodes = spec.nodes.map(keep).filter(Boolean);
  return sceneGlb({ ...spec, nodes }, { uids: true, live: true, content, knownImages });
}

/** A full live frame as Angelo writes one: every node `content`, with digests, no stubs. */
export function fullGlb(spec) {
  return sceneGlb(spec, { uids: true, live: true });
}

/** The digests of the images a spec's materials use (what a page that showed it has). */
export function imageDigests(spec) {
  const found = new Set();
  for (const def of Object.values(spec.materials ?? {})) {
    if (!def.texture) continue;
    const texture = spec.textures?.[def.texture] ?? { size: 64 };
    found.add(sha256(texture.png ?? noisePng(texture.size ?? 64, texture.seed ?? 1)));
  }
  return found;
}
