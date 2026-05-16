"use strict";
import * as THREE from "three";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { MTLLoader } from "three/addons/loaders/MTLLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const modal    = document.getElementById("preview-modal");
const canvas   = document.getElementById("preview-canvas");
const closeBtn = document.getElementById("preview-close");
const loading  = document.getElementById("preview-loading");

let renderer = null;
let animId   = null;
let cleanup  = null;

window.openPreview = function (jobId) {
    modal.classList.remove("hidden");
    loading.classList.remove("hidden");

    if (cleanup) { cleanup(); cleanup = null; }

    const w = window.innerWidth;
    const h = window.innerHeight;

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h, false);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111827);

    const camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 1e7);

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(1, 2, 1.5);
    scene.add(sun);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.screenSpacePanning = false;

    const mtlLoader = new MTLLoader();
    mtlLoader.load(`/api/jobs/${jobId}/files/model.mtl`, (materials) => {
        materials.preload();
        const objLoader = new OBJLoader();
        objLoader.setMaterials(materials);
        objLoader.load(`/api/jobs/${jobId}/files/model.obj`, (obj) => {
            obj.rotation.x = -Math.PI / 2;  // Z-up (print coords) → Y-up (Three.js)
            scene.add(obj);
            loading.classList.add("hidden");

            const box = new THREE.Box3().setFromObject(obj);
            const center = box.getCenter(new THREE.Vector3());
            const size   = box.getSize(new THREE.Vector3());

            // Centre model at origin
            obj.position.sub(center);

            const maxDim = Math.max(size.x, size.z);
            const fovRad = (camera.fov * Math.PI) / 180;
            const dist   = (maxDim / 2 / Math.tan(fovRad / 2)) * 1.4;

            camera.position.set(0, dist * 0.45, dist);
            camera.near = dist / 1000;
            camera.far  = dist * 10;
            camera.updateProjectionMatrix();
            camera.lookAt(0, 0, 0);
            controls.target.set(0, 0, 0);
            controls.update();
        });
    });

    const ro = new ResizeObserver(() => {
        const rw = window.innerWidth;
        const rh = window.innerHeight;
        camera.aspect = rw / rh;
        camera.updateProjectionMatrix();
        renderer.setSize(rw, rh, false);
    });
    ro.observe(document.documentElement);

    function animate() {
        animId = requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    }
    animate();

    cleanup = () => {
        ro.disconnect();
        cancelAnimationFrame(animId);
        animId = null;
        controls.dispose();
        renderer.dispose();
        renderer = null;
    };
};

function closePreview() {
    modal.classList.add("hidden");
    if (cleanup) { cleanup(); cleanup = null; }
}

closeBtn.addEventListener("click", closePreview);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePreview(); });
