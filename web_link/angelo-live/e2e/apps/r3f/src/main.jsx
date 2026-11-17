// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// A React Three Fiber scene: drei's useGLTF loads the robot, <primitive> shows it, and the live link tracks what
// useGLTF returned. The app gives the head its own material and hangs a halo on the antenna.

import { StrictMode, Suspense, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Environment, Lightformer, OrbitControls, useGLTF } from "@react-three/drei";
import { GLTFLoader } from "three-stdlib";
import * as THREE from "three";
import { createAngeloLive } from "@call-me-sensei/angelo";

const url = "/models/robot.glb";

function Robot() {
  const gltf = useGLTF(url);
  const { gl, scene, camera } = useThree();
  useEffect(() => {
    const head = gltf.scene.getObjectByName("Head");
    head.material = new THREE.MeshPhysicalMaterial({ color: 0xf2f2f7, clearcoat: 1, roughness: 0.2 });
    const halo = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.03, 8, 32), new THREE.MeshBasicMaterial({ color: 0xffcc00 }));
    halo.name = "Halo";
    halo.position.y = 0.3;
    halo.rotation.x = Math.PI / 2;
    gltf.scene.getObjectByName("Antenna").add(halo);
    // The loader drei uses (three-stdlib's GLTFLoader), so Angelo's frames decode as useGLTF's did.
    const live = createAngeloLive({ renderer: gl, scene, loader: new GLTFLoader() });
    live.track(gltf, url);
    let cancelled = false;
    if (import.meta.env.DEV) {
      import("./test-hook.js").then(({ installTestHook }) => {
        if (!cancelled) installTestHook({ live, renderer: gl, url, roots: () => [gltf.scene], camera: () => camera, instances: 1 });
      });
    }
    return () => {
      cancelled = true;
      halo.removeFromParent();
      live.stop();
    };
  }, [gltf, gl, scene, camera]);
  useFrame((_, delta) => {
    gltf.scene.getObjectByName("Eye_L")?.rotateY(delta);
  });
  return <primitive object={gltf.scene} />;
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Canvas camera={{ position: [3, 2.4, 4.5], fov: 40 }}>
      <ambientLight intensity={0.4} />
      <directionalLight position={[3, 5, 2]} intensity={2} />
      <Suspense fallback={null}>
        <Environment resolution={64}>
          <Lightformer intensity={2} position={[0, 4, 4]} scale={[6, 2, 1]} />
        </Environment>
        <Robot />
      </Suspense>
      <OrbitControls target={[0, 1.2, 0]} />
    </Canvas>
  </StrictMode>,
);
