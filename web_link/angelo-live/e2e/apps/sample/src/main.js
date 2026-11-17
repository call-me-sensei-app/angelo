// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The development sample: one model, a clone of it, a material and a child of the developer's own.

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { createAngeloLive } from "@call-me-sensei/angelo";

const query = new URLSearchParams(location.search);
const model = query.get("model") || "sample.glb";
const copies = Number(query.get("copies") ?? 2);
const url = `/models/${model}`;
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x202124);
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 200);
camera.position.set(0, 2.5, 7);
camera.lookAt(0, 0.8, 0);
scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2), new THREE.DirectionalLight(0xffffff, 2));

const loader = new GLTFLoader();
const gltf = await loader.loadAsync(url);
scene.add(gltf.scene);
const clone = copies > 1 ? gltf.scene.clone() : null;
if (clone) {
  clone.position.x = 3.2;
  scene.add(clone);
}

// The developer's own changes, which the live link must keep.
const shade = gltf.scene.getObjectByName("Shade");
if (shade) shade.material = new THREE.MeshNormalMaterial();
const arm = gltf.scene.getObjectByName("Arm");
const marker = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.1, 0.1), new THREE.MeshBasicMaterial({ color: 0xff00ff }));
marker.name = "devMarker";
marker.position.set(0.3, 0, 0);
arm?.add(marker);

const live = createAngeloLive({ renderer, scene, loader });
live.track(gltf, url);
if (clone) live.track(clone, url);

renderer.setAnimationLoop(() => renderer.render(scene, camera));

if (import.meta.env.DEV) {
  const { installTestHook } = await import("./test-hook.js");
  installTestHook({ live, renderer, url, roots: () => [gltf.scene, clone].filter(Boolean), camera: () => camera,
    instances: clone ? 2 : 1,
    info: { developerMaterial: "Shade", developerChild: "devMarker" } });
}
