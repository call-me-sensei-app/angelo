// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// Angelo's live web preview: the latest export in the bundled Three.js build, reloaded when Angelo says it changed
// (server-sent events), beside its report. Everything is served by Angelo on this computer; nothing loads from a CDN.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { WebShaders } from "./shaders.js";
import { setupPanel } from "./panel.js";

const LIBS = "./vendor/three/examples/jsm/libs/";
const ENVIRONMENT = 0.6;   // The neutral studio light the preview adds (the world isn't exported; the report says so).
const $ = (id) => document.getElementById(id);
const panel = setupPanel();   // First, so a column the person hid stays hidden while the scene loads.

const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.AgXToneMapping;   // Blender's default view transform.
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.setClearColor(0x000000, 0);

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = ENVIRONMENT;
const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 2000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;

const draco = new DRACOLoader().setDecoderPath(LIBS + "draco/gltf/");   // WebAssembly: the bundled decoder.
const ktx2 = new KTX2Loader().setTranscoderPath(LIBS + "basis/").detectSupport(renderer);
const loader = new GLTFLoader().setDRACOLoader(draco).setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
const shaders = new WebShaders(renderer, camera);
const CAPTURE_SIDE = 1280;   // The longest side of the picture Angelo asks for (angelo_view image WEB).
let latest = null, sceneVersion = -1;

let content = null, mixer = null, version = -1, framed = false, loading = false, pending = false, text = {};
const timer = new THREE.Timer();

function resize() {
  const { clientWidth: w, clientHeight: h } = canvas;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(canvas);

function frame(root, gltf) {
  // First load only: later reloads keep the person's view while they edit in Blender.
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = box.getSize(new THREE.Vector3()).length() / 2 || 1;
  const shot = gltf.cameras.find((item) => item.isPerspectiveCamera);
  if (shot) {   // Start from the scene's camera, as Blender renders it.
    shot.updateWorldMatrix(true, false);
    camera.position.setFromMatrixPosition(shot.matrixWorld);
    camera.quaternion.setFromRotationMatrix(shot.matrixWorld);
    camera.fov = shot.fov;
    const ahead = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
    controls.target.copy(camera.position).addScaledVector(ahead, camera.position.distanceTo(center));
  } else {
    camera.position.copy(center).add(new THREE.Vector3(1, 0.7, 1).normalize().multiplyScalar(radius * 2.6));
    controls.target.copy(center);
  }
  camera.near = radius / 200;
  camera.far = radius * 200;
  camera.updateProjectionMatrix();
  controls.update();
  framed = true;
}

async function loadScene() {
  if (loading) { pending = true; return; }
  loading = true;
  $("loading").classList.add("on");
  try {
    const response = await fetch("scene.glb", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const gltf = await loader.parseAsync(await response.arrayBuffer(), "./");
    const root = gltf.scene;
    root.traverse((node) => {
      if (node.isMesh) { node.castShadow = true; node.receiveShadow = true; }
      if (node.isDirectionalLight || node.isSpotLight || node.isPointLight) {
        node.castShadow = true;
        node.shadow.mapSize.set(2048, 2048);
        node.shadow.bias = -0.0005;
      }
    });
    if (content) { scene.remove(content); dispose(content); }
    content = root;
    scene.add(root);
    mixer = gltf.animations.length ? new THREE.AnimationMixer(root) : null;
    gltf.animations.forEach((clip) => mixer.clipAction(clip).play());
    if (!framed) frame(root, gltf);
    if (latest) applyShaders(latest);
    $("empty").hidden = !new THREE.Box3().setFromObject(root).isEmpty();
  } catch (error) {
    console.warn("Angelo web preview: the scene did not load", error);
  } finally {
    loading = false;
    $("loading").classList.remove("on");
    if (pending) { pending = false; loadScene(); }
  }
}

function dispose(root) {
  root.traverse((node) => {
    if (!node.isMesh) return;
    node.geometry.dispose();
    for (const material of new Set([].concat(node.material, node.userData.angeloStandIn || []))) {
      for (const value of Object.values(material)) if (value && value.isTexture) value.dispose();
      material.dispose();
    }
  });
}

function number(value) {
  return new Intl.NumberFormat(document.documentElement.lang || undefined).format(value);
}

function bytes(value) {
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return (index ? value.toFixed(1) : String(value)) + " " + units[index];
}

function status(kind, label) {
  const node = $("status");
  node.className = "eyebrow " + kind;
  node.textContent = label;
  panel.status(kind);   // The hidden column's button keeps the live state's dot.
}

function show(report) {
  text = report.strings || text;
  panel.text(text);
  if (report.lang) document.documentElement.lang = report.lang;
  document.title = "Angelo · " + (text.title || "Web Preview");
  $("title").textContent = text.title || "";
  for (const [id, key] of [["budget-title", "budget"], ["carry-title", "carry"], ["settings-title", "settings"],
    ["l-download", "download"], ["l-triangles", "triangles"], ["l-draw", "draw_calls"], ["l-textures", "textures"],
    ["disclaimer", "disclaimer"]]) $(id).textContent = text[key] || "";
  $("empty").textContent = text.empty || "";
  if (report.status === "failed") { status("down", text.failed || ""); return; }
  const budget = report.budget || {};
  $("v-download").textContent = budget.bytes != null ? bytes(budget.bytes) : "–";
  $("v-triangles").textContent = budget.triangles != null ? number(budget.triangles) : "–";
  $("v-draw").textContent = budget.draw_calls != null ? number(budget.draw_calls) : "–";
  $("v-textures").textContent = budget.textures ? number(budget.textures.count) + " · " + bytes(budget.textures.bytes) : "–";
  const merged = (report.merged || {}).objects || 0;
  $("merged").hidden = !merged;
  $("merged").textContent = (text.merged || "") + ": " + number(merged);
  const list = $("findings");
  list.replaceChildren();
  for (const item of report.findings || []) {
    const row = document.createElement("li");
    row.className = item.severity;
    const message = document.createElement("span");
    message.className = "msg";
    message.textContent = item.message;
    const fix = document.createElement("span");
    fix.className = "fix";
    const label = document.createElement("b");
    label.textContent = (text.fix || "") + " ";
    fix.append(label, item.fix);
    row.append(message, fix);
    list.append(row);
  }
  if (!list.children.length) {
    const row = document.createElement("li");
    row.className = "clear";
    row.textContent = text.none || "";
    list.append(row);
  }
  const settings = report.settings || {};
  $("chips").replaceChildren(...[[text.meshes, settings.meshes], [text.images, settings.textures]]
    .filter(([, value]) => value).map(([label, value]) => {
      const chip = document.createElement("span");
      chip.textContent = label + " · " + value;
      return chip;
    }));
  const when = new Date((report.updated || Date.now() / 1000) * 1000)
    .toLocaleTimeString(document.documentElement.lang || undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  status("live", (text.live || "") + " · " + (text.updated || "{time}").replace("{time}", when));
}

function applyShaders(report) {
  // Web shaders swap in over the stand-ins; their compile status goes back to Angelo.
  if (!content) return;
  const statuses = shaders.apply(content, report);
  fetch("feedback", { method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ version: report.version, shaders: statuses }) }).catch(() => {});
}

async function capture() {
  // A picture of the canvas as drawn now, for Angelo's agent (never stored by the page). A canvas with no size (a
  // hidden tab) sends nothing; Angelo then says the page drew nothing.
  if (!canvas.width || !canvas.height) return;
  renderer.render(scene, camera);
  const scale = Math.min(1, CAPTURE_SIDE / Math.max(canvas.width, canvas.height));
  const copy = document.createElement("canvas");
  copy.width = Math.max(1, Math.round(canvas.width * scale));
  copy.height = Math.max(1, Math.round(canvas.height * scale));
  const context = copy.getContext("2d");
  context.fillStyle = getComputedStyle(document.body).backgroundColor;
  context.fillRect(0, 0, copy.width, copy.height);
  context.drawImage(canvas, 0, 0, copy.width, copy.height);
  const blob = await new Promise((done) => copy.toBlob(done, "image/png"));
  if (blob) fetch("capture", { method: "POST", headers: { "Content-Type": "image/png" }, body: blob }).catch(() => {});
}

async function refresh() {
  const response = await fetch("report.json", { cache: "no-store" });
  const report = await response.json();
  if (report.version === version) return;
  const hadScene = version >= 0;
  version = report.version;
  latest = report;
  show(report);
  if (report.status === "failed" && hadScene) return;
  if (report.scene_version !== undefined && report.scene_version === sceneVersion && content) {
    applyShaders(report);   // A uniform edit or a shader file save: no new export to load.
    return;
  }
  sceneVersion = report.scene_version;
  await loadScene();
}

const events = new EventSource("events");
events.addEventListener("version", () => { status("busy", text.updating || ""); refresh(); });
events.addEventListener("capture", () => { capture(); });
events.addEventListener("closed", () => { status("down", text.offline || ""); events.close(); });
events.onerror = () => status("down", text.offline || "");   // It reconnects by itself while Angelo runs.

renderer.setAnimationLoop(() => {
  timer.update();
  if (mixer) mixer.update(timer.getDelta());
  shaders.tick(timer.getElapsed(), canvas.width, canvas.height, content);
  controls.update();
  renderer.render(scene, camera);
});
resize();
refresh();
