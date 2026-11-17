// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// One parse of an asset described as glTF nodes (matcher.js matches them against the developer's copies).

/** The node's name as Angelo wrote it: GLTFLoader keeps it in userData.name and sanitizes Object3D.name. */
export function nameOf(object) {
  return typeof object.userData?.name === "string" ? object.userData.name : object.name;
}

export function isDrawable(object) {
  return Boolean(object && (object.isMesh || object.isPoints || object.isLine || object.isSprite) && object.geometry);
}

/** What kind of Object3D this is, as far as updating it in place goes: containers are interchangeable. */
export function kindOf(object) {
  if (object.isBone) return "Bone";
  if (object.isSkinnedMesh) return "SkinnedMesh";
  if (object.isInstancedMesh) return "InstancedMesh";
  if (object.isMesh) return "Mesh";
  if (object.isPoints) return "Points";
  if (object.isLine || object.isLight || object.isCamera || object.isSprite) return object.type;
  return "Container";
}

function uidOf(object) {
  const uid = object.userData?.angelo_uid;
  return typeof uid === "string" && uid.length <= 512 ? uid : null;
}

/**
 * The content GLTFLoader made under a node object for the node itself, from the node's glTF definition: the
 * primitives of a multi-primitive mesh (the first non-node drawable children, as many as the mesh has primitives;
 * the loader adds them before child nodes), and the camera or light beside a mesh.
 */
function contentOf(object, nodeDef, json, isNode) {
  const content = { primitives: [], camera: null, light: null };
  if (!nodeDef) return content;
  const own = object.children.filter((child) => !isNode(child));
  if (nodeDef.mesh !== undefined && !isDrawable(object)) {
    const count = json?.meshes?.[nodeDef.mesh]?.primitives?.length ?? Infinity;
    content.primitives = own.filter(isDrawable).slice(0, count);
  }
  if (nodeDef.camera !== undefined && !object.isCamera) content.camera = own.find((child) => child.isCamera) ?? null;
  if (nodeDef.extensions?.KHR_lights_punctual && !object.isLight) {
    content.light = own.find((child) => child.isLight) ?? null;
  }
  return content;
}

/**
 * The nodes of a loaded glTF, parents first: {object, parent, name, uid, role, digest, content, nameChain, uidChain}
 * (role and digest: PROTOCOL.md §1.2, from extras), plus
 * `byObject`. Without `parser.associations` (another loader) every object under the scene counts as a node.
 */
export function describe(gltf) {
  const root = gltf.scene ?? gltf;
  const associations = gltf.parser?.associations;
  const json = gltf.parser?.json;
  const indexOf = (object) => associations?.get(object)?.nodes;
  const isNode = associations ? (object) => indexOf(object) !== undefined : () => true;
  const nodes = [];
  const byObject = new Map();
  const visit = (parentObject, parentNode, prefixName, prefixUid) => {
    const seenName = new Map();
    const seenUid = new Map();
    for (const child of parentObject.children) {
      if (!isNode(child)) continue;
      const name = nameOf(child);
      const uid = uidOf(child);
      const n = seenName.get(name) ?? 0;
      seenName.set(name, n + 1);
      const u = seenUid.get(uid ?? "") ?? 0;
      seenUid.set(uid ?? "", u + 1);
      const nameChain = `${prefixName}\u0000${name}#${n}`;
      const uidChain = `${prefixUid}\u0000${uid ?? ""}#${u}`;
      const nodeDef = associations ? json?.nodes?.[indexOf(child)] : null;
      const extras = child.userData ?? {};
      const node = { object: child, parent: parentNode, name, uid, nameChain, uidChain,
        role: extras.angelo_role === "parent" ? "parent" : "content",
        digest: typeof extras.angelo_digest === "string" ? extras.angelo_digest.slice(0, 256) : null,
        content: contentOf(child, nodeDef, json, isNode) };
      nodes.push(node);
      byObject.set(child, node);
      visit(child, node, nameChain, uidChain);
    }
  };
  visit(root, null, "", "");
  return { root, nodes, byObject };
}
