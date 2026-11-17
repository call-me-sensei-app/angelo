// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Editing an existing .glb the way Angelo's live frames change one (PROTOCOL.md §1.2), for tests on assets we didn't
// build (a downloaded character): stamp keys, roles and digests, change a material or a mesh, rename and reparent,
// and cut a delta (changed nodes as content, their ancestors and their skins' joints as parent locators).

import { createHash } from "node:crypto";

const sha256 = (data) => createHash("sha256").update(data).digest("hex");
const COMPONENTS = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };
const BYTES = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };

export function readGlb(buffer) {
  const jsonLength = buffer.readUInt32LE(12);
  const json = JSON.parse(buffer.subarray(20, 20 + jsonLength).toString("utf8"));
  const binStart = 20 + jsonLength;
  const bin = binStart < buffer.length ? Buffer.from(buffer.subarray(binStart + 8, binStart + 8 + buffer.readUInt32LE(binStart)))
    : Buffer.alloc(0);
  return { json, bin };
}

export function writeGlb({ json, bin }) {
  const out = { ...json, buffers: [{ byteLength: bin.length }] };
  const text = Buffer.from(JSON.stringify(out), "utf8");
  const jsonChunk = Buffer.concat([text, Buffer.alloc((4 - (text.length % 4)) % 4, 0x20)]);
  const binChunk = Buffer.concat([bin, Buffer.alloc((4 - (bin.length % 4)) % 4)]);
  const head = (length, type) => {
    const value = Buffer.alloc(8);
    value.writeUInt32LE(length, 0);
    value.writeUInt32LE(type, 4);
    return value;
  };
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonChunk.length + 8 + binChunk.length, 8);
  return Buffer.concat([header, head(jsonChunk.length, 0x4e4f534a), jsonChunk, head(binChunk.length, 0x004e4942), binChunk]);
}

export function clone(document) {
  return { json: JSON.parse(JSON.stringify(document.json)), bin: document.bin };
}

function accessorBytes({ json, bin }, index) {
  const accessor = json.accessors[index];
  const view = json.bufferViews[accessor.bufferView];
  const size = COMPONENTS[accessor.type] * BYTES[accessor.componentType];
  const stride = view.byteStride ?? size;
  const start = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0);
  const out = Buffer.alloc(accessor.count * size);
  for (let i = 0; i < accessor.count; i++) bin.copy(out, i * size, start + i * stride, start + i * stride + size);
  return out;
}

function appendAccessor(document, accessor, bytes) {
  const pad = (4 - (document.bin.length % 4)) % 4;
  document.bin = Buffer.concat([document.bin, Buffer.alloc(pad), bytes]);
  document.json.bufferViews.push({ buffer: 0, byteOffset: document.bin.length - bytes.length, byteLength: bytes.length });
  const { byteOffset: _unused, sparse: _sparse, ...rest } = accessor;
  document.json.accessors.push({ ...rest, bufferView: document.json.bufferViews.length - 1 });
  return document.json.accessors.length - 1;
}

/** Parent index of each node. */
export function parents(json) {
  const found = new Map();
  json.nodes.forEach((node, index) => (node.children ?? []).forEach((child) => found.set(child, index)));
  return found;
}

/** A node's index: `node` is an index, or a name (the first node with it; names may repeat, so prefer indices). */
export function nodeIndex(json, node) {
  return typeof node === "number" ? node : json.nodes.findIndex((item) => item.name === node);
}

/**
 * Give every node its key (bones as "<armature>/<bone>", the rest "n<index>", as Angelo keys them: unique per
 * file), and its role and digest; give every image its digest. `content` (node indices) marks the rest as parents.
 */
export function stamp(document, { content = null, knownImages = null, keys = defaultKeys(document.json),
  digests = null } = {}) {
  const { json } = document;
  for (const image of json.images ?? []) {
    const bytes = image.bufferView !== undefined ? accessorlessView(document, image.bufferView) : Buffer.from(image.uri ?? "");
    const digest = sha256(bytes);
    image.extras = { ...(image.extras ?? {}), angelo_digest: digest };
    if (knownImages?.has(digest)) image.extras.angelo_stub = true;
  }
  json.nodes.forEach((node, index) => {
    const role = !content || content.has(index) ? "content" : "parent";
    node.extras = { ...(node.extras ?? {}), angelo_uid: keys[index], angelo_role: role };
    if (role === "content") node.extras.angelo_digest = digests?.[index] ?? nodeDigest(document, index, keys);
  });
  delete json.animations; // Live frames carry no animations.
  return document;
}

function accessorlessView({ json, bin }, index) {
  const view = json.bufferViews[index];
  return bin.subarray(view.byteOffset ?? 0, (view.byteOffset ?? 0) + view.byteLength);
}

export function defaultKeys(json) {
  const joints = new Set((json.skins ?? []).flatMap((skin) => skin.joints));
  const up = parents(json);
  return json.nodes.map((node, index) => {
    if (!joints.has(index)) return `n${index}`;
    let armature = index;
    while (up.has(armature) && joints.has(up.get(armature))) armature = up.get(armature);
    armature = up.get(armature) ?? armature;
    return `n${armature}/${node.name}`;
  });
}

function nodeDigest(document, index, keys) {
  const { json } = document;
  const node = json.nodes[index];
  const hash = createHash("sha256");
  const { children, extras: _extras, ...own } = node;
  hash.update(JSON.stringify({ own, children: (children ?? []).map((child) => keys[child]) }));
  if (node.mesh !== undefined) {
    const mesh = json.meshes[node.mesh];
    for (const primitive of mesh.primitives) {
      hash.update(JSON.stringify({ mode: primitive.mode, material: json.materials?.[primitive.material] }));
      for (const accessor of [...Object.values(primitive.attributes), primitive.indices].filter((item) => item !== undefined)) {
        hash.update(accessorBytes(document, accessor));
      }
    }
  }
  if (node.skin !== undefined) hash.update(JSON.stringify(json.skins[node.skin].joints.map((joint) => keys[joint])));
  return hash.digest("hex");
}

/** Give `nodeName`'s primitives their own materials (copies), so recolouring it changes nothing else. */
export function ownMaterials(document, node) {
  const { json } = document;
  const nodeName = json.nodes[nodeIndex(json, node)].name;
  const mesh = json.meshes[json.nodes[nodeIndex(json, node)].mesh];
  for (const primitive of mesh.primitives) {
    if (primitive.material === undefined) continue;
    json.materials.push({ ...JSON.parse(JSON.stringify(json.materials[primitive.material])),
      name: `${json.materials[primitive.material].name ?? "material"} (${nodeName})` });
    primitive.material = json.materials.length - 1;
  }
  return document;
}

export function recolor(document, node, rgba) {
  const { json } = document;
  const mesh = json.meshes[json.nodes[nodeIndex(json, node)].mesh];
  for (const primitive of mesh.primitives) {
    const material = json.materials[primitive.material];
    material.pbrMetallicRoughness = { ...(material.pbrMetallicRoughness ?? {}), baseColorFactor: rgba };
    delete material.pbrMetallicRoughness.baseColorTexture;
  }
  return document;
}

/** A different vertex count: the first primitive gets `extra` copies of its first triangle's vertices. */
export function regrow(document, node, extra = 3) {
  const { json } = document;
  const index = nodeIndex(json, node);
  const meshIndex = json.nodes[index].mesh;
  const mesh = JSON.parse(JSON.stringify(json.meshes[meshIndex]));
  const primitive = mesh.primitives[0];
  const count = json.accessors[primitive.attributes.POSITION].count;
  const indices = primitive.indices !== undefined ? accessorBytes(document, primitive.indices) : null;
  const firstVertices = [];
  for (let i = 0; i < extra; i++) {
    if (!indices) firstVertices.push(i % count);
    else {
      const indexAccessor = json.accessors[primitive.indices];
      const width = BYTES[indexAccessor.componentType];
      firstVertices.push(width === 4 ? indices.readUInt32LE((i % 3) * 4) : width === 2 ? indices.readUInt16LE((i % 3) * 2) : indices[i % 3]);
    }
  }
  for (const [name, accessorIndex] of Object.entries(primitive.attributes)) {
    const accessor = json.accessors[accessorIndex];
    const size = COMPONENTS[accessor.type] * BYTES[accessor.componentType];
    const bytes = accessorBytes(document, accessorIndex);
    const added = Buffer.concat(firstVertices.map((vertex) => bytes.subarray(vertex * size, (vertex + 1) * size)));
    const next = { ...accessor, count: count + extra };
    primitive.attributes[name] = appendAccessor(document, next, Buffer.concat([bytes, added]));
  }
  const newIndices = Buffer.alloc(4 * (extra - (extra % 3)));
  for (let i = 0; i < newIndices.length / 4; i++) newIndices.writeUInt32LE(count + i, i * 4);
  if (indices) {
    const indexAccessor = json.accessors[primitive.indices];
    const width = BYTES[indexAccessor.componentType];
    const wide = Buffer.alloc((indices.length / width) * 4);
    for (let i = 0; i < indices.length / width; i++) {
      wide.writeUInt32LE(width === 4 ? indices.readUInt32LE(i * 4) : width === 2 ? indices.readUInt16LE(i * 2) : indices[i], i * 4);
    }
    const all = Buffer.concat([wide, newIndices]);
    primitive.indices = appendAccessor(document, { componentType: 5125, type: "SCALAR", count: all.length / 4 }, all);
  }
  json.meshes.push(mesh);
  json.nodes[index].mesh = json.meshes.length - 1;
  return document;
}

export function renameReparent(document, node, newName, newParentIndex) {
  const { json } = document;
  const index = nodeIndex(json, node);
  const up = parents(json);
  if (up.has(index)) {
    const old = json.nodes[up.get(index)];
    old.children = old.children.filter((child) => child !== index);
    if (!old.children.length) delete old.children;
  } else {
    json.scenes[0].nodes = json.scenes[0].nodes.filter((node) => node !== index);
  }
  (json.nodes[newParentIndex].children ??= []).push(index);
  json.nodes[index].name = newName;
  return document;
}

/**
 * A delta of `document` (already edited): the nodes in `changed` (indices) as content; their ancestors, and the
 * joints (with ancestors) of their skins, as parent locators (empties).
 */
export function delta(document, changed, options = {}) {
  const { json } = document;
  const keys = defaultKeys(json);
  const up = parents(json);
  const keep = new Set();
  const withAncestors = (index) => {
    for (let at = index; at !== undefined && !keep.has(at); at = up.get(at)) keep.add(at);
  };
  for (const index of changed) {
    withAncestors(index);
    const skin = json.nodes[index].skin;
    if (skin !== undefined) json.skins[skin].joints.forEach(withAncestors);
  }
  const order = [...keep].sort((a, b) => a - b);
  // Digests as the full scene has them (a node's digest covers all its children's keys, not just those sent).
  const fullDigests = json.nodes.map((_, index) => nodeDigest(document, index, keys));
  const remap = new Map(order.map((index, i) => [index, i]));
  const content = new Set(changed.map((index) => remap.get(index)));
  const out = clone(document);
  out.json.nodes = order.map((index) => {
    const node = JSON.parse(JSON.stringify(json.nodes[index]));
    if (node.children) node.children = node.children.filter((child) => keep.has(child)).map((child) => remap.get(child));
    if (!node.children?.length) delete node.children;
    if (!content.has(remap.get(index))) {
      delete node.mesh;
      delete node.skin;
      delete node.camera;
      delete node.extensions;
    }
    return node;
  });
  out.json.scenes = [{ nodes: json.scenes[0].nodes.filter((index) => keep.has(index)).map((index) => remap.get(index)) }];
  out.json.scene = 0;
  const skins = [];
  for (const node of out.json.nodes) {
    if (node.skin === undefined) continue;
    const skin = JSON.parse(JSON.stringify(json.skins[node.skin]));
    skin.joints = skin.joints.map((joint) => remap.get(joint));
    if (skin.skeleton !== undefined) skin.skeleton = remap.get(skin.skeleton);
    skins.push(skin);
    node.skin = skins.length - 1;
  }
  out.json.skins = skins;
  if (!skins.length) delete out.json.skins;
  return stamp(out, { ...options, content, keys: order.map((index) => keys[index]),
    digests: order.map((index) => fullDigests[index]) });
}

export function digestOf(bytes) {
  return sha256(bytes);
}
