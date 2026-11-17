// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Textures by content (PROTOCOL.md §1.2). Each texture of a parse gets its image's digest: Angelo's
// `extras.angelo_digest` (SHA-256 of the image's bytes), else, for a file Angelo didn't stamp (the developer's own
// saved file), a hash of the bytes. A new parse's texture whose picture (and sampler) the page already has is
// replaced by the page's own Texture, so an unchanged image is never uploaded again. A stub (`extras.angelo_stub`, a
// 1 x 1 PNG) must be a picture the page has; otherwise the page asks for a full frame. The page keeps its textures
// by digest for the whole link, even after the objects that used them go (Resources frees their GPU memory only).

const digests = new WeakMap(); // Texture -> image digest

function samplerOf(texture) {
  return [texture.wrapS, texture.wrapT, texture.magFilter, texture.minFilter, texture.flipY, texture.colorSpace,
    texture.channel, texture.anisotropy, texture.generateMipmaps, texture.rotation, ...texture.offset.toArray(),
    ...texture.repeat.toArray(), ...texture.center.toArray()].join(",");
}

/** The texture's identity as a picture on the GPU: its image's digest and its sampler; null when unknown. */
export function textureKey(texture) {
  const digest = digests.get(texture);
  return digest ? `${digest}|${samplerOf(texture)}` : null;
}

/** SHA-256 hex of the bytes: what Angelo's `angelo_digest` is, so a saved file's pictures match a live frame's. */
async function hashBytes(buffer) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", buffer));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function imageOf(json, textureIndex) {
  const def = json?.textures?.[textureIndex];
  if (!def) return null;
  const source = def.source ?? Object.values(def.extensions ?? {}).find((ext) => ext?.source !== undefined)?.source;
  return source === undefined ? null : { index: source, def: json.images?.[source] };
}

/**
 * Give every texture of a GLTFLoader result its digest. Returns [{texture, stub}]: `stub` when the image carried
 * only its digest (no bytes), so its picture must come from the page's cache.
 */
export async function digestTextures(gltf) {
  const parser = gltf.parser;
  const found = [];
  if (!parser?.associations || !parser.json) return found;
  const byImage = new Map();
  for (const [key, value] of parser.associations) {
    if (!key?.isTexture || value?.textures === undefined) continue;
    const image = imageOf(parser.json, value.textures);
    if (!image?.def) continue;
    const extras = image.def.extras ?? {};
    const stub = extras.angelo_stub === true; // A 1 x 1 placeholder: the picture must be one the page has.
    let digest = typeof extras.angelo_digest === "string" ? extras.angelo_digest.slice(0, 128) : byImage.get(image.index);
    if (!digest) {
      if (image.def.bufferView !== undefined) {
        digest = await hashBytes(await parser.getDependency("bufferView", image.def.bufferView));
      } else if (typeof image.def.uri === "string") {
        digest = image.def.uri.startsWith("data:") ? await hashBytes(new TextEncoder().encode(image.def.uri))
          : `u${image.def.uri}`;
      }
    }
    if (!digest) continue;
    byImage.set(image.index, digest);
    digests.set(key, digest);
    found.push({ texture: key, stub });
  }
  // A stub the loader couldn't decode leaves no texture behind: the material lacks the picture, so the frame can't
  // be shown as sent. Reported as a stub the page doesn't have, which makes the page ask for a full frame.
  const loaded = new Set([...parser.associations.values()].map((value) => value?.textures).filter((i) => i !== undefined));
  (parser.json.textures ?? []).forEach((def, index) => {
    const image = imageOf(parser.json, index);
    if (image?.def?.extras?.angelo_stub === true && !loaded.has(index)) found.push({ texture: null, stub: true });
  });
  return found;
}

function eachTexture(material, visit) {
  for (const [name, value] of Object.entries(material)) if (value?.isTexture) visit(value, (next) => { material[name] = next; });
  for (const uniform of Object.values(material.uniforms ?? {})) {
    if (uniform?.value?.isTexture) visit(uniform.value, (next) => { uniform.value = next; });
  }
}

/** The page's textures by key, so new materials can reuse the ones already on the GPU. */
export class TextureCache {
  constructor() {
    this.byKey = new Map();
    this.digests = new Set(); // Image digests the page holds a texture of.
  }

  remember(texture) {
    const key = textureKey(texture);
    if (key && !this.byKey.has(key)) {
      this.byKey.set(key, texture);
      this.digests.add(digests.get(texture));
    }
  }

  hasDigest(digest) {
    return this.digests.has(digest);
  }

  has(texture) {
    if (!texture) return false;
    const key = textureKey(texture);
    return Boolean(key && this.byKey.has(key));
  }

  /** Point `material` at the page's own textures where it has the same picture; remember the rest. */
  substitute(material) {
    for (const item of Array.isArray(material) ? material : [material]) {
      if (!item) continue;
      eachTexture(item, (texture, replace) => {
        const key = textureKey(texture);
        const own = key && this.byKey.get(key);
        if (own && own !== texture) replace(own);
        else this.remember(texture);
      });
    }
  }

  /** True for a texture the cache keeps for the link (its GPU memory may be freed, never the texture). */
  retains(texture) {
    const key = textureKey(texture);
    return Boolean(key && this.byKey.get(key) === texture);
  }

  /** The link ended: nothing more will come as a stub. */
  clear() {
    this.byKey.clear();
    this.digests.clear();
  }
}
