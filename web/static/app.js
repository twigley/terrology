"use strict";

// ── Map setup ──────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: true }).setView([54, -2], 6);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© <a href='https://openstreetmap.org/copyright'>OpenStreetMap</a> contributors",
    maxZoom: 19,
}).addTo(map);

let pin = null;
let square = null;
let currentLat = null;
let currentLon = null;

// ── DOM refs ───────────────────────────────────────────────────────────────
const searchInput   = document.getElementById("search");
const searchBtn     = document.getElementById("search-btn");
const coordsDisplay = document.getElementById("coords-display");
const coordsText    = document.getElementById("coords-text");
const noPinMsg      = document.getElementById("no-pin-msg");
const radiusSlider  = document.getElementById("radius");
const radiusVal     = document.getElementById("radius-val");
const exagSlider    = document.getElementById("exag");
const exagVal       = document.getElementById("exag-val");
const colorsSelect  = document.getElementById("colors");
const buildingsBox  = document.getElementById("buildings");
const roofBox       = document.getElementById("roof-shapes");
const contourInput  = document.getElementById("contour");
const scaleDisplay  = document.getElementById("scale-display");
const minFeature    = document.getElementById("min-feature");
const generateBtn   = document.getElementById("generate-btn");
const statusArea    = document.getElementById("status-area");
const statusIcon    = document.getElementById("status-icon");
const statusMsg     = document.getElementById("status-msg");
const elapsedDiv    = document.getElementById("elapsed");
const downloadBtn   = document.getElementById("download-btn");

// ── Helpers ────────────────────────────────────────────────────────────────
function squareBounds(lat, lon, r) {
    const dlat = r / 111111;
    const dlon = r / (111111 * Math.cos(lat * Math.PI / 180));
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]];
}

function updateScale() {
    const r = parseInt(radiusSlider.value, 10);
    const s = Math.round(r * 2000 / 190);
    scaleDisplay.textContent = s.toLocaleString();
    minFeature.textContent = Math.max(1, Math.round(r / 300));
}

function setPin(lat, lon) {
    currentLat = lat;
    currentLon = lon;
    const r = parseInt(radiusSlider.value, 10);
    const bounds = squareBounds(lat, lon, r);

    if (pin) pin.setLatLng([lat, lon]);
    else pin = L.marker([lat, lon]).addTo(map);

    if (square) square.setBounds(bounds);
    else square = L.rectangle(bounds, { color: "#3b82f6", fillOpacity: 0.08, weight: 2 }).addTo(map);

    coordsText.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    coordsDisplay.classList.remove("hidden");
    noPinMsg.classList.add("hidden");
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate terrain map";
}

function updateSquare() {
    const r = parseInt(radiusSlider.value, 10);
    radiusVal.textContent = r;
    if (square && currentLat !== null) square.setBounds(squareBounds(currentLat, currentLon, r));
    updateScale();
}

// ── Map click ──────────────────────────────────────────────────────────────
map.on("click", (e) => setPin(e.latlng.lat, e.latlng.lng));

// ── Slider events ──────────────────────────────────────────────────────────
radiusSlider.addEventListener("input", updateSquare);
exagSlider.addEventListener("input", () => {
    exagVal.textContent = parseFloat(exagSlider.value).toFixed(1);
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
    const body = {
        lat: currentLat,
        lon: currentLon,
        radius: parseInt(radiusSlider.value, 10),
        terrain_exag: parseFloat(exagSlider.value),
        colors: parseInt(colorsSelect.value, 10),
        no_buildings: !buildingsBox.checked,
        roof_shapes: roofBox.checked,
        contour_interval: contourVal,
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
