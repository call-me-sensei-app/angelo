// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// A tiny physics game: cannon-es bodies for the level's crates and barrel, synced onto the loaded meshes every
// frame, a health bar over one crate, a toon barrel, and a follow camera. Objects Angelo deletes lose their bodies.

import * as THREE from "three";
import * as CANNON from "cannon-es";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { createAngeloLive } from "@call-me-sensei/angelo";

const url = "/models/level.glb";
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x87b5e5);
scene.fog = new THREE.Fog(0x87b5e5, 12, 40);
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(5, 10, 4);
scene.add(sun, new THREE.AmbientLight(0xffffff, 0.6));
const camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 0.1, 100);

const loader = new GLTFLoader();
const gltf = await loader.loadAsync(url);
scene.add(gltf.scene);
gltf.scene.updateMatrixWorld(true);

const world = new CANNON.World({ gravity: new CANNON.Vec3(0, -9.82, 0) });
const bodies = new Map(); // mesh -> body: the game's own references to the linked meshes.
function addBody(mesh, mass) {
  const size = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3());
  const body = new CANNON.Body({ mass, shape: new CANNON.Box(new CANNON.Vec3(size.x / 2, size.y / 2, size.z / 2)) });
  const position = mesh.getWorldPosition(new THREE.Vector3());
  body.position.set(position.x, position.y, position.z);
  world.addBody(body);
  bodies.set(mesh, body);
}
for (const mesh of gltf.scene.getObjectByName("Level").children) {
  if (!mesh.isMesh) continue;
  addBody(mesh, mesh.name === "Ground" ? 0 : 2);
}

gltf.scene.getObjectByName("Barrel").material = new THREE.MeshToonMaterial({ color: 0x8a8f99 });
const bar = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.12), new THREE.MeshBasicMaterial({ color: 0x22dd44 }));
bar.name = "HealthBar";
bar.position.set(0, 0.8, 0);
gltf.scene.getObjectByName("Crate_2").add(bar);

const live = createAngeloLive({ renderer, scene, loader });
live.track(gltf, url);
live.on("removed", () => {
  for (const [mesh, body] of bodies) {
    let inScene = false;
    for (let object = mesh; object; object = object.parent) if (object === scene) inScene = true;
    if (!inScene) {
      world.removeBody(body);
      bodies.delete(mesh);
    }
  }
});
live.on("added", ({ objects }) => objects.filter((object) => object.isMesh).forEach((mesh) => addBody(mesh, 2)));

const clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  world.fixedStep(1 / 60, Math.min(clock.getDelta(), 0.1));
  for (const [mesh, body] of bodies) {
    if (body.mass === 0) continue;
    // The body moves the mesh in world space; the meshes hang under the level node, which doesn't move.
    mesh.position.copy(mesh.parent.worldToLocal(new THREE.Vector3().copy(body.position)));
    mesh.quaternion.copy(body.quaternion);
  }
  camera.position.set(6 * Math.sin(clock.elapsedTime * 0.1), 5, 8);
  camera.lookAt(0, 0.5, 0);
  renderer.render(scene, camera);
});

if (import.meta.env.DEV) {
  const { installTestHook } = await import("./test-hook.js");
  installTestHook({ live, renderer, url, roots: () => [gltf.scene], camera: () => camera, instances: 1,
    info: { held: () => [...bodies.keys()].map((mesh) => mesh.id) } });
}
