// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// A product configurator: its own lights and environment, a toon ShaderMaterial on the upper, colour variants for
// the laces (changed in place on the loaded material), a price tag hung on the tongue, orbit controls.

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { createAngeloLive } from "@call-me-sensei/angelo";

const url = "/models/sneaker.glb";
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.shadowMap.enabled = true;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xe9ecef);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
const key = new THREE.SpotLight(0xffffff, 40, 20, Math.PI / 5, 0.4);
key.position.set(3, 5, 4);
key.castShadow = true;
scene.add(key, new THREE.HemisphereLight(0xffffff, 0x8899aa, 0.6));
const floor = new THREE.Mesh(new THREE.CircleGeometry(4, 48), new THREE.MeshStandardMaterial({ color: 0xdfe3e8 }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

const camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 0.1, 100);
camera.position.set(2.8, 2.2, 4.2);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0.5, 0);
controls.enableDamping = true;

const loader = new GLTFLoader();
const gltf = await loader.loadAsync(url);
gltf.scene.traverse((object) => {
  if (object.isMesh) object.castShadow = object.receiveShadow = true;
});
scene.add(gltf.scene);

const toon = new THREE.ShaderMaterial({
  uniforms: { tint: { value: new THREE.Color(0x3b82f6) }, light: { value: new THREE.Vector3(0.5, 0.8, 0.4).normalize() } },
  vertexShader: `varying vec3 vNormal; void main() { vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `uniform vec3 tint; uniform vec3 light; varying vec3 vNormal; void main() {
    float d = dot(normalize(vNormal), light); float band = d > 0.6 ? 1.0 : d > 0.2 ? 0.7 : 0.45;
    gl_FragColor = vec4(tint * band, 1.0); }`,
});
gltf.scene.getObjectByName("Upper").material = toon;

const tag = new THREE.Mesh(new THREE.PlaneGeometry(0.4, 0.25), new THREE.MeshBasicMaterial({ color: 0xffd400, side: THREE.DoubleSide }));
tag.name = "PriceTag";
tag.position.set(0, 1.2, 0);
gltf.scene.getObjectByName("Tongue").add(tag);

const variants = { white: 0xffffff, red: 0xd62828, black: 0x111111 };
const laces = gltf.scene.getObjectByName("Laces");
const ui = document.getElementById("ui");
for (const [label, colour] of Object.entries(variants)) {
  const button = document.createElement("button");
  button.textContent = `Laces: ${label}`;
  button.onclick = () => laces.material.color.setHex(colour);
  ui.appendChild(button);
}
laces.material.color.setHex(variants.red); // The shopper's pick, made on the loaded material itself.

const live = createAngeloLive({ renderer, scene, loader });
live.track(gltf, url);
live.on("added", ({ objects }) => objects.forEach((object) => object.traverse((item) => {
  if (item.isMesh) item.castShadow = item.receiveShadow = true;
})));

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});

if (import.meta.env.DEV) {
  const { installTestHook } = await import("./test-hook.js");
  installTestHook({ live, renderer, url, roots: () => [gltf.scene], camera: () => camera, instances: 1,
    info: { tint: { node: "Laces", color: "d62828" } } });
}
