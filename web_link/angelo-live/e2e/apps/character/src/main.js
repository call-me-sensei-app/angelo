// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// A rigged character running in place: an AnimationMixer plays its run clip in a loop while Angelo edits it.

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { createAngeloLive } from "@call-me-sensei/angelo";

const model = new URLSearchParams(location.search).get("model") || "character.glb";
const url = `/models/${model}`;
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x30343a);
scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 2.2), new THREE.DirectionalLight(0xffffff, 1.6));

const loader = new GLTFLoader();
const gltf = await loader.loadAsync(url);
scene.add(gltf.scene);
const box = new THREE.Box3().setFromObject(gltf.scene);
const size = box.getSize(new THREE.Vector3()).length();
const center = box.getCenter(new THREE.Vector3());
const camera = new THREE.PerspectiveCamera(40, innerWidth / innerHeight, size / 100, size * 10);
camera.position.copy(center).add(new THREE.Vector3(0, size * 0.1, size * 1.3));
camera.lookAt(center);

const mixer = new THREE.AnimationMixer(gltf.scene);
const clip = gltf.animations.find((item) => /run/i.test(item.name)) ?? gltf.animations[0];
if (clip) mixer.clipAction(clip).play();
const clock = new THREE.Clock();

const live = createAngeloLive({ renderer, scene, loader });
live.track(gltf, url);

renderer.setAnimationLoop(() => {
  mixer.update(clock.getDelta());
  renderer.render(scene, camera);
});

if (import.meta.env.DEV) {
  const { installTestHook } = await import("./test-hook.js");
  installTestHook({ live, renderer, url, roots: () => [gltf.scene], camera: () => camera, mixer: () => mixer,
    instances: 1, info: { clip: clip?.name ?? null } });
}
