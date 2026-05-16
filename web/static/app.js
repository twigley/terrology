"use strict";

// ── Map setup ──────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: true }).setView([54, -2], 6);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© <a href='https://openstreetmap.org/copyright'>OpenStreetMap</a> contributors",
    maxZoom: 19,
}).addTo(map);

let pin = null;
let previewLayer = null;
let currentLat = null;
let currentLon = null;
let currentShape = "square";

// ── DOM refs ───────────────────────────────────────────────────────────────
const searchInput   = document.getElementById("search");
const searchBtn     = document.getElementById("search-btn");
const coordsDisplay = document.getElementById("coords-display");
const coordsText    = document.getElementById("coords-text");
const noPinMsg      = document.getElementById("no-pin-msg");
const radiusSlider  = document.getElementById("radius");
const radiusVal     = document.getElementById("radius-val");
const shapeControl  = document.getElementById("shape-control");
const exagSlider    = document.getElementById("exag");
const exagVal       = document.getElementById("exag-val");
const bldgExagSlider = document.getElementById("bldg-exag");
const bldgExagVal   = document.getElementById("bldg-exag-val");
const colorsSelect  = document.getElementById("colors");
const buildingsBox  = document.getElementById("buildings");
const roofBox       = document.getElementById("roof-shapes");
const contourInput  = document.getElementById("contour");
const waterSlider   = document.getElementById("water-depth");
const waterVal      = document.getElementById("water-val");
const borderSlider  = document.getElementById("border-width");
const borderVal     = document.getElementById("border-val");
const demSelect     = document.getElementById("dem-source");
const demHint       = document.getElementById("dem-hint");
const scaleDisplay  = document.getElementById("scale-display");
const minFeature    = document.getElementById("min-feature");
const generateBtn   = document.getElementById("generate-btn");
const statusArea    = document.getElementById("status-area");
const statusIcon    = document.getElementById("status-icon");
const statusMsg     = document.getElementById("status-msg");
const elapsedDiv    = document.getElementById("elapsed");
const downloadBtn   = document.getElementById("download-btn");

// ── Preview geometry helpers ───────────────────────────────────────────────
function squareBounds(lat, lon, r) {
    const dlat = r / 111111;
    const dlon = r / (111111 * Math.cos(lat * Math.PI / 180));
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]];
}

function hexPoints(lat, lon, r) {
    const pts = [];
    for (let i = 0; i < 6; i++) {
        const angle = i * 2 * Math.PI / 6;  // flat top + bottom
        const dlat = r * Math.sin(angle) / 111111;
        const dlon = r * Math.cos(angle) / (111111 * Math.cos(lat * Math.PI / 180));
        pts.push([lat + dlat, lon + dlon]);
    }
    return pts;
}

function updateScale() {
    const r = parseInt(radiusSlider.value, 10);
    const s = Math.round(r * 2000 / 190);
    scaleDisplay.textContent = s.toLocaleString();
    minFeature.textContent = Math.max(1, Math.round(r / 300));
}

function buildPreviewLayer(lat, lon, r, shape) {
    if (shape === "circle") {
        return L.circle([lat, lon], { radius: r, color: "#3b82f6", fillOpacity: 0.08, weight: 2 });
    } else if (shape === "hexagon") {
        return L.polygon(hexPoints(lat, lon, r), { color: "#3b82f6", fillOpacity: 0.08, weight: 2 });
    } else {
        return L.rectangle(squareBounds(lat, lon, r), { color: "#3b82f6", fillOpacity: 0.08, weight: 2 });
    }
}

function refreshPreview() {
    if (currentLat === null) return;
    const r = parseInt(radiusSlider.value, 10);
    if (previewLayer) { map.removeLayer(previewLayer); }
    previewLayer = buildPreviewLayer(currentLat, currentLon, r, currentShape);
    previewLayer.addTo(map);
}

function setPin(lat, lon) {
    currentLat = lat;
    currentLon = lon;

    if (pin) pin.setLatLng([lat, lon]);
    else pin = L.marker([lat, lon]).addTo(map);

    refreshPreview();

    coordsText.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    coordsDisplay.classList.remove("hidden");
    noPinMsg.classList.add("hidden");
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate terrain map";
}

// ── Map click ──────────────────────────────────────────────────────────────
map.on("click", (e) => setPin(e.latlng.lat, e.latlng.lng));

// ── Slider / control events ────────────────────────────────────────────────
radiusSlider.addEventListener("input", () => {
    radiusVal.textContent = radiusSlider.value;
    refreshPreview();
    updateScale();
});

exagSlider.addEventListener("input", () => {
    exagVal.textContent = parseFloat(exagSlider.value).toFixed(1);
});

bldgExagSlider.addEventListener("input", () => {
    const v = parseFloat(bldgExagSlider.value);
    bldgExagVal.textContent = v === 0 ? "match terrain" : `${v.toFixed(1)}×`;
});

waterSlider.addEventListener("input", () => {
    waterVal.textContent = parseFloat(waterSlider.value).toFixed(1);
});

borderSlider.addEventListener("input", () => {
    borderVal.textContent = parseInt(borderSlider.value, 10);
});

// ── Shape selector ─────────────────────────────────────────────────────────
shapeControl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    shapeControl.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentShape = btn.dataset.shape;
    refreshPreview();
});

// ── Nominatim search ───────────────────────────────────────────────────────
async function doSearch() {
    const q = searchInput.value.trim();
    if (!q) return;
    searchBtn.disabled = true;
    searchBtn.textContent = "…";
    try {
        const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(q)}&format=json&limit=1`;
        const res = await fetch(url, { headers: { "Accept-Language": "en" } });
        const data = await res.json();
        if (!data.length) { alert(`No results for "${q}"`); return; }
        const { lat, lon, boundingbox } = data[0];
        const llat = parseFloat(lat), llon = parseFloat(lon);
        setPin(llat, llon);
        if (boundingbox) {
            map.fitBounds([[parseFloat(boundingbox[0]), parseFloat(boundingbox[2])],
                           [parseFloat(boundingbox[1]), parseFloat(boundingbox[3])]],
                          { maxZoom: 14 });
        } else {
            map.setView([llat, llon], 13);
        }
    } catch {
        alert("Search failed — check your connection.");
    } finally {
        searchBtn.disabled = false;
        searchBtn.textContent = "Go";
    }
}

searchBtn.addEventListener("click", doSearch);
searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ── Job polling ────────────────────────────────────────────────────────────
let pollTimer = null;

function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function showStatus(icon, msg, elapsed, showDownload, jobId) {
    statusArea.classList.remove("hidden");
    statusIcon.innerHTML = icon;
    statusMsg.textContent = msg;
    elapsedDiv.textContent = elapsed || "";
    if (showDownload && jobId) {
        downloadBtn.href = `/api/jobs/${jobId}/download`;
        downloadBtn.classList.remove("hidden");
    } else {
        downloadBtn.classList.add("hidden");
    }
}

async function pollStatus(jobId) {
    try {
        const res = await fetch(`/api/jobs/${jobId}`);
        if (!res.ok) { stopPolling(); showStatus("❌", "Job lost (server restarted?)", ""); return; }
        const data = await res.json();
        const elapsed = data.elapsed_s != null ? `${Math.round(data.elapsed_s)}s elapsed` : "";
        if (data.status === "queued") {
            showStatus('<span class="spinner"></span>', "Queued…", "");
        } else if (data.status === "running") {
            showStatus('<span class="spinner"></span>', "Generating…", elapsed);
        } else if (data.status === "ready") {
            stopPolling();
            showStatus("✅", "Ready to download", elapsed, true, jobId);
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate terrain map";
        } else if (data.status === "error") {
            stopPolling();
            showStatus("❌", "Error", "", false);
            elapsedDiv.textContent = data.error || "Unknown error";
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate terrain map";
        }
    } catch {
        // network blip — keep polling
    }
}

// ── Generate ───────────────────────────────────────────────────────────────
generateBtn.addEventListener("click", async () => {
    if (currentLat == null) return;
    stopPolling();
    generateBtn.disabled = true;
    generateBtn.textContent = "Submitting…";
    downloadBtn.classList.add("hidden");

    const contourVal = contourInput.value ? parseFloat(contourInput.value) : null;
    const bldgExagRaw = parseFloat(bldgExagSlider.value);

    const body = {
        lat: currentLat,
        lon: currentLon,
        radius: parseInt(radiusSlider.value, 10),
        shape: currentShape,
        terrain_exag: parseFloat(exagSlider.value),
        colors: parseInt(colorsSelect.value, 10),
        no_buildings: !buildingsBox.checked,
        roof_shapes: roofBox.checked,
        contour_interval: contourVal,
        building_exag: bldgExagRaw > 0 ? bldgExagRaw : null,
        water_depth_mm: parseFloat(waterSlider.value),
        border_width_mm: parseInt(borderSlider.value, 10),
        dem_source: demSelect.value,
    };

    try {
        const res = await fetch("/api/jobs", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (res.status === 429) {
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate terrain map";
            showStatus("⚠️", "Rate limit reached", "Try again in an hour.");
            return;
        }
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const { job_id } = await res.json();
        showStatus('<span class="spinner"></span>', "Queued…", "");
        pollTimer = setInterval(() => pollStatus(job_id), 2000);
    } catch (err) {
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
        showStatus("❌", "Submission failed", err.message);
    }
});

// ── Init ───────────────────────────────────────────────────────────────────
updateScale();

(async () => {
    try {
        const res = await fetch("/api/dem-sources");
        const data = await res.json();
        if (!data.key_configured) {
            demHint.classList.remove("hidden");
            Array.from(demSelect.options).forEach(opt => {
                const src = data.sources.find(s => s.id === opt.value);
                if (src && !src.available) {
                    opt.disabled = true;
                    opt.text += " (API key not configured)";
                }
            });
        }
    } catch { /* leave defaults */ }
})();
