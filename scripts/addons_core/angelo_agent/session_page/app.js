// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// An app session's live page: the conversation as Angelo publishes it (state.json, refreshed when the event stream
// says it changed) beside the scene in 3D (the live preview's own export, in its look). Read-only: nothing here sends
// anything to Angelo. Everything is served by Angelo on this computer; nothing loads from elsewhere.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Look } from "./preview/look.js";
import { ownPresses } from "./preview/drag.js";

const $ = (id) => document.getElementById(id);
const LIBS = "./preview/vendor/three/examples/jsm/libs/";
let state = { strings: {} };

function text(key, fields = {}) {
  // The page's words come translated with the state; placeholders are filled here.
  const template = (state.strings && state.strings[key]) || "";
  return template.replace(/\{(\w+)\}/g, (match, name) => (name in fields ? String(fields[name]) : match));
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

// The scene -----------------------------------------------------------------------------------------------------------
// The 3D view starts on its own, guarded: a browser that can't draw it (WebGL off or unavailable) still shows the
// session, and the scene's place says why it is empty (the 2.0.1 audit, P2-7).
const canvas = $("view");
let view = null;
let content = null, framed = false, sceneVersion = -1, loading = false, again = false;

function startScene() {
  try {
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.AgXToneMapping;
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const look = new Look(renderer, scene);
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 2000);
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    ownPresses(canvas);
    const loader = new GLTFLoader()
      .setDRACOLoader(new DRACOLoader().setDecoderPath(LIBS + "draco/gltf/"))
      .setKTX2Loader(new KTX2Loader().setTranscoderPath(LIBS + "basis/").detectSupport(renderer))
      .setMeshoptDecoder(MeshoptDecoder);
    const resize = () => {
      const { clientWidth: w, clientHeight: h } = canvas;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
    };
    new ResizeObserver(resize).observe(canvas);
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
    return { renderer, scene, look, camera, controls, loader };
  } catch (error) {
    console.warn("Angelo live page: this browser can't draw the 3D scene", error);
    return null;
  }
}

function frame(root) {
  const { camera, controls } = view;
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = box.getSize(new THREE.Vector3()).length() / 2 || 1;
  camera.position.copy(center).add(new THREE.Vector3(1, 0.7, 1).normalize().multiplyScalar(radius * 2.6));
  controls.target.copy(center);
  camera.near = radius / 200;
  camera.far = radius * 200;
  camera.updateProjectionMatrix();
  framed = true;
}

function dispose(root) {
  // A replaced scene's geometry, materials and textures, freed (the live preview's own pattern; the audit, P2-8).
  root.traverse((node) => {
    if (!node.isMesh) return;
    node.geometry.dispose();
    for (const material of new Set([].concat(node.material, node.userData.angeloStandIn || []))) {
      for (const value of Object.values(material)) if (value && value.isTexture) value.dispose();
      material.dispose();
    }
  });
}

async function loadScene() {
  if (!view) return;
  if (loading) { again = true; return; }
  loading = true;
  $("loading").classList.add("on");
  try {
    const response = await fetch("scene.glb", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const gltf = await view.loader.parseAsync(await response.arrayBuffer(), "./");
    if (content) { view.scene.remove(content); dispose(content); }
    content = gltf.scene;
    view.scene.add(content);
    if (!framed) frame(content);   // Later updates keep the view the person turned to.
    await view.look.apply(gltf);
    $("scene-hint").hidden = true;
  } catch (error) {
    if (!content) { $("scene-hint").hidden = false; $("scene-hint").textContent = text("scene_none"); }
  } finally {
    loading = false;
    $("loading").classList.remove("on");
    if (again) { again = false; loadScene(); }
  }
}

view = startScene();

// The session ---------------------------------------------------------------------------------------------------------
const MARKS = { running: "live", stopping: "live", waiting_for_approval: "wait", waiting_for_person: "wait", done: "done" };
let elapsedAt = 0, elapsedBase = 0, turnBase = null;

function renderRows(rows) {
  const list = $("rows");
  const near = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.replaceChildren(...rows.map((row) => {
    const item = document.createElement("li");
    item.className = row.role === "tool" ? `tool ${row.status}` : row.role;
    if (row.role === "tool") {
      const name = document.createElement("code");
      name.textContent = row.tool;
      const status = document.createElement("span");
      status.textContent = text("tool_" + row.status) || row.status;
      item.append(name, status);
      return item;
    }
    const who = document.createElement("b");
    who.textContent = row.role === "app" ? state.app || "" : "Angelo";   // The app's name, or Angelo's own.
    item.append(who, document.createTextNode(row.text || ""));
    return item;
  }));
  if (near) list.scrollTop = list.scrollHeight;   // Follow the stream unless the person scrolled back.
}

function inline(target, line) {
  // **bold**, *italic* or _italic_ and `code` as elements; everything else as text, as written.
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\*([^*\s][^*]*)\*|\b_([^_\s][^_]*)_\b/g;
  let at = 0;
  for (const match of line.matchAll(pattern)) {
    if (match.index > at) target.append(document.createTextNode(line.slice(at, match.index)));
    const node = document.createElement(match[1] !== undefined ? "strong" : match[2] !== undefined ? "code" : "em");
    node.textContent = match[1] ?? match[2] ?? match[3] ?? match[4];
    target.append(node);
    at = match.index + match[0].length;
  }
  if (at < line.length) target.append(document.createTextNode(line.slice(at)));
}

function markdown(container, text) {
  // The agent's words in a small, safe subset of markdown, built as elements and text (never markup): paragraphs,
  // bullets, bold, italic and inline code, as Angelo's chat shows the same reply.
  container.replaceChildren();
  let paragraph = null, list = null;
  for (const line of String(text || "").replace(/\r\n?/g, "\n").split("\n")) {
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (bullet) {
      paragraph = null;
      if (!list) { list = document.createElement("ul"); container.append(list); }
      const item = document.createElement("li");
      inline(item, bullet[1]);
      list.append(item);
    } else if (!line.trim()) {
      paragraph = list = null;
    } else {
      list = null;
      if (paragraph) paragraph.append(document.createElement("br"));
      else { paragraph = document.createElement("p"); container.append(paragraph); }
      inline(paragraph, line);
    }
  }
}

function renderViews(ids) {
  $("views-panel").hidden = ids.length === 0;   // Never an empty panel over the scene.
  $("no-views").hidden = ids.length > 0;
  const list = $("views");
  const shown = [...list.children].map((item) => item.dataset.id).join(",");
  if (shown === ids.join(",")) return;
  list.replaceChildren(...ids.map((id) => {
    const item = document.createElement("li");
    item.dataset.id = id;
    const picture = document.createElement("img");
    picture.src = "picture/" + encodeURIComponent(id);
    picture.alt = "";
    picture.draggable = false;
    item.append(picture);
    return item;
  }));
}

function renderGoal(goal) {
  $("goal").hidden = !goal;
  if (!goal) return;
  $("goal-title").textContent = text("goal");
  $("goal-objective").textContent = goal.objective;
  const percent = (value) => Math.round((value || 0) * 100);
  $("goal-bar").style.width = percent(goal.best) + "%";
  $("goal-pass").style.left = percent(goal.pass_at) + "%";
  const facts = [text("round", { round: goal.round })];
  if (goal.score != null) facts.push(text("score", { score: percent(goal.score) }));
  if (goal.best != null) facts.push(text("best", { score: percent(goal.best) }));
  if (goal.pass_at != null) facts.push(text("pass_at", { score: percent(goal.pass_at) }));
  $("goal-facts").textContent = facts.join(" · ");
}

function renderTime() {
  const passed = (performance.now() - elapsedAt) / 1000;
  $("v-elapsed").textContent = turnBase == null ? clock(elapsedBase + passed)
    : `${clock(elapsedBase + passed)} · ${text("turn_elapsed", { time: clock(turnBase + passed) })}`;
}
setInterval(renderTime, 1000);

function render() {
  document.title = `Angelo · ${text("title")}`;
  $("app").textContent = text("with_app", { app: state.app || "" });
  $("status").textContent = text("status_" + state.status);
  $("dot").className = "dot " + (MARKS[state.status] || "");
  $("step").hidden = !state.step;
  $("step-label").textContent = text("step");
  $("step-tool").textContent = state.step || "";
  $("l-elapsed").textContent = text("elapsed", { time: "" }).trim();
  elapsedBase = state.elapsed || 0;
  turnBase = state.turn_elapsed;
  elapsedAt = performance.now();
  renderTime();
  const usage = state.usage;
  $("usage-row").hidden = !usage;
  if (usage) {
    $("l-usage").textContent = text("usage");
    $("v-usage").textContent = [usage.tokens, usage.money].filter(Boolean).join(" · ");   // This conversation's only.
  }
  renderGoal(state.goal);
  $("result").hidden = !state.result;
  $("result-title").textContent = text("result");
  markdown($("result-text"), state.result || "");
  $("stream-title").textContent = text("stream");
  $("views-title").textContent = text("views");
  $("no-views").textContent = text("no_views");
  $("read-only").textContent = text("read_only");
  renderRows(state.rows || []);
  renderViews(state.pictures || []);
  if (!content) {   // Loading, or this browser can't draw it: everything else still updates.
    $("scene-hint").hidden = false;
    $("scene-hint").textContent = text(view ? "scene_loading" : "scene_unavailable");
  }
  if (view && state.scene_version && state.scene_version !== sceneVersion) {
    sceneVersion = state.scene_version;
    loadScene();
  }
}

async function refresh() {
  try {
    const response = await fetch("state.json", { cache: "no-store" });
    if (!response.ok) return ended();
    state = await response.json();
    render();
  } catch (error) { /* Angelo away for a moment: the stream reconnects. */ }
}

function ended() {
  $("ended").hidden = false;
  $("ended-text").textContent = text("closed");
  $("dot").className = "dot off";
}

const events = new EventSource("events");
events.addEventListener("version", refresh);
events.addEventListener("closed", () => { events.close(); ended(); });
events.onerror = () => { $("dot").className = "dot off"; $("app").textContent = text("offline"); };
refresh();
