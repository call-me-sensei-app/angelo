// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The scene's look, as the export writes it into the glTF scene's extras (angelo_look, doc/angelo/web_export.rst):
// three.js tone mapping, exposure and output colour space, and the World as the environment that lights the scene
// and shows behind it. A developer's page reads the same extras the same way. The person can light the scene with
// the neutral studio instead, and turn the background off; both choices stay in this page's storage.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const KEY = "angelo.web-preview.look";
const STUDIO = 0.6;   // The neutral studio's intensity, as the preview lit every scene before 1.0.9.
const FLAT_SIZE = [64, 32];   // A plain colour World as an equirect: every direction the same light.

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

function save(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* Storage off: the choice lasts this visit. */ }
}

// A Radiance .hdr (RGBE; flat or run-length scanlines) as half-float RGBA rows, the first row the top of the sky,
// decoded as three.js's HDRLoader decodes it so the page shows what a developer's page would.
export function parseRadiance(buffer) {
  const bytes = new Uint8Array(buffer);
  let at = 0;
  const line = () => {
    let text = "";
    while (at < bytes.length && bytes[at] !== 10) text += String.fromCharCode(bytes[at++]);
    at += 1;
    return text;
  };
  if (!line().startsWith("#?")) throw new Error("not a Radiance file");
  for (let text = line(); text !== ""; text = line()) {
    if (text.startsWith("FORMAT=") && text !== "FORMAT=32-bit_rle_rgbe") throw new Error("not RGBE");
    if (at >= bytes.length) throw new Error("no size line");
  }
  const size = /^-Y (\d+) \+X (\d+)$/.exec(line());
  if (!size) throw new Error("unsupported orientation");
  const height = Number(size[1]), width = Number(size[2]);
  const rgbe = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    const row = rgbe.subarray(y * width * 4, (y + 1) * width * 4);
    if (width >= 8 && width <= 0x7fff && bytes[at] === 2 && bytes[at + 1] === 2 && !(bytes[at + 2] & 0x80)) {
      at += 4;
      for (let channel = 0; channel < 4; channel += 1) {
        for (let x = 0; x < width;) {
          let count = bytes[at++];
          if (count > 128) {
            count -= 128;
            const value = bytes[at++];
            for (let i = 0; i < count && x < width; i += 1) row[(x++) * 4 + channel] = value;
          } else {
            for (let i = 0; i < count && x < width; i += 1) row[(x++) * 4 + channel] = bytes[at++];
          }
        }
      }
    } else {
      row.set(bytes.subarray(at, at + width * 4));
      at += width * 4;
    }
  }
  const data = new Uint16Array(width * height * 4);
  for (let i = 0; i < width * height * 4; i += 4) {
    const scale = Math.pow(2, rgbe[i + 3] - 128) / 255;
    data[i] = THREE.DataUtils.toHalfFloat(Math.min(rgbe[i] * scale, 65504));
    data[i + 1] = THREE.DataUtils.toHalfFloat(Math.min(rgbe[i + 1] * scale, 65504));
    data[i + 2] = THREE.DataUtils.toHalfFloat(Math.min(rgbe[i + 2] * scale, 65504));
    data[i + 3] = THREE.DataUtils.toHalfFloat(1);
  }
  return { width, height, data };
}

function equirect(width, height, data) {
  const texture = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.HalfFloatType);
  texture.colorSpace = THREE.LinearSRGBColorSpace;
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.minFilter = texture.magFilter = THREE.LinearFilter;
  texture.flipY = true;   // The file's first row is the top of the sky.
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  return texture;
}

function flat(color) {
  const [width, height] = FLAT_SIZE;
  const data = new Uint16Array(width * height * 4);
  const halves = [...color.slice(0, 3), 1].map((value) => THREE.DataUtils.toHalfFloat(Math.max(0, value)));
  for (let i = 0; i < data.length; i += 4) data.set(halves, i);
  return equirect(width, height, data);
}

export class Look {
  constructor(renderer, scene) {
    this.renderer = renderer;
    this.scene = scene;
    this.pmrem = new THREE.PMREMGenerator(renderer);
    this.studio = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.world = null;   // { background, lighting, intensity } of the World the export carries.
    this.settings = null;
    const saved = load();
    this.choice = { light: saved.light === "studio" ? "studio" : "world", background: saved.background !== false };
    this.forced = false;  // The Compare view shows the scene as Angelo does: the World, its background.
    this.listeners = [];
  }

  // Apply the loaded glTF's look; resolves once its World is ready. A missing look (an export from before 1.0.9)
  // keeps the preview's earlier defaults: AgX and the studio.
  async apply(gltf) {
    const look = gltf.scene.userData.angelo_look || null;
    this.settings = look;
    const renderer = this.renderer;
    renderer.toneMapping = (look && THREE[look.toneMapping]) ?? THREE.AgXToneMapping;
    renderer.toneMappingExposure = look && Number.isFinite(look.toneMappingExposure) ? look.toneMappingExposure : 1;
    renderer.outputColorSpace = look && look.outputColorSpace === THREE.LinearSRGBColorSpace
      ? THREE.LinearSRGBColorSpace : THREE.SRGBColorSpace;
    const environment = look ? look.environment : undefined;
    if (environment && environment.type === "pending") { this.update(); return; }   // Keep the World shown now.
    this.disposeWorld();
    if (environment && environment.type === "color") {
      this.world = this.lit(flat(environment.color), environment.intensity);
    } else if (environment && environment.type === "equirect" && Number.isInteger(environment.bufferView)) {
      try {
        const buffer = await gltf.parser.getDependency("bufferView", environment.bufferView);
        const { width, height, data } = parseRadiance(buffer);
        this.world = this.lit(equirect(width, height, data), environment.intensity);
      } catch (error) {
        console.warn("Angelo web preview: the World's picture did not load", error);
      }
    }
    this.update();
  }

  lit(texture, intensity) {
    const lighting = this.pmrem.fromEquirectangular(texture).texture;
    return { background: texture, lighting, intensity: Number.isFinite(intensity) ? intensity : 1 };
  }

  disposeWorld() {
    if (!this.world) return;
    this.world.background.dispose();
    this.world.lighting.dispose();
    this.world = null;
  }

  // The person's choices (or the Compare view's forced World) onto the scene.
  update() {
    // A file without a look (exported before 1.0.9) has no World to light it: the studio.
    const light = this.forced ? "world" : this.settings ? this.choice.light : "studio";
    const shown = light === "world" && (this.forced ? Boolean(this.settings && this.settings.background)
      : this.choice.background && Boolean(this.settings ? this.settings.background : true));
    const scene = this.scene;
    if (light === "studio") {
      scene.environment = this.studio;
      scene.environmentIntensity = STUDIO;
    } else {
      scene.environment = this.world ? this.world.lighting : null;
      scene.environmentIntensity = this.world ? this.world.intensity : 1;
    }
    scene.background = shown && this.world ? this.world.background : null;
    scene.backgroundIntensity = this.world ? this.world.intensity : 1;
    for (const listener of this.listeners) listener();
  }

  set(choice) {
    Object.assign(this.choice, choice);
    save(this.choice);
    this.update();
  }

  force(on) {
    this.forced = on;
    this.update();
  }

  onChange(listener) { this.listeners.push(listener); }

  // What the page applied, for Angelo (the shader report's ``look``, angelo_view WEB): never the person's data.
  report() {
    const names = Object.fromEntries(["NoToneMapping", "LinearToneMapping", "ReinhardToneMapping", "CineonToneMapping",
      "ACESFilmicToneMapping", "AgXToneMapping", "NeutralToneMapping"].map((name) => [THREE[name], name]));
    const environment = this.settings && this.settings.environment;
    return { toneMapping: names[this.renderer.toneMapping] || "", exposure: this.renderer.toneMappingExposure,
      outputColorSpace: this.renderer.outputColorSpace, environment: this.world ? (environment || {}).type || "" : "",
      light: this.forced ? "world" : this.settings ? this.choice.light : "studio",
      background: Boolean(this.scene.background) };
  }
}
