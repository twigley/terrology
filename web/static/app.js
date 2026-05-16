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
let currentMode = "pin";       // "pin" | "area"
let currentPolygon = null;     // [[lng, lat], ...] when drawn
let drawHandler = null;
const drawnItems = new L.FeatureGroup().addTo(map);

// ── DOM refs ───────────────────────────────────────────────────────────────
const searchInput    = document.getElementById("search");
const searchBtn      = document.getElementById("search-btn");
const modeControl    = document.getElementById("mode-control");
const coordsDisplay  = document.getElementById("coords-display");
const coordsLabel    = document.getElementById("coords-label");
const coordsText     = document.getElementById("coords-text");
const clearPolygon   = document.getElementById("clear-polygon");
const noPinMsg       = document.getElementById("no-pin-msg");
const drawHint       = document.getElementById("draw-hint");
const pinOptions     = document.getElementById("pin-options");
const radiusSlider   = document.getElementById("radius");
const radiusVal      = document.getElementById("radius-val");
const shapeControl   = document.getElementById("shape-control");
const exagSlider     = document.getElementById("exag");
const exagVal        = document.getElementById("exag-val");
const bldgExagSlider = document.getElementById("bldg-exag");
const bldgExagVal    = document.getElementById("bldg-exag-val");
const colorsSelect   = document.getElementById("colors");
const buildingsBox   = document.getElementById("buildings");
const roofBox        = document.getElementById("roof-shapes");
const contourInput   = document.getElementById("contour");
const waterSlider    = document.getElementById("water-depth");
const waterVal       = document.getElementById("water-val");
const borderSlider   = document.getElementById("border-width");
const borderVal      = document.getElementById("border-val");
const demSelect      = document.getElementById("dem-source");
const demHint        = document.getElementById("dem-hint");
const scaleDisplay   = document.getElementById("scale-display");
const minFeature     = document.getElementById("min-feature");
const generateBtn    = document.getElementById("generate-btn");
const previewBtn     = document.getElementById("preview-btn");
const statusArea     = document.getElementById("status-area");
const statusIcon     = document.getElementById("status-icon");
const statusMsg      = document.getElementById("status-msg");
const elapsedDiv     = document.getElementById("elapsed");
const downloadBtn    = document.getElementById("download-btn");

// ── Preview geometry helpers ───────────────────────────────────────────────
function squareBounds(lat, lon, r) {
    const dlat = r / 111111;
    const dlon = r / (111111 * Math.cos(lat * Math.PI / 180));
    return [[lat - dlat, lon - dlon], [lat + dlat, lon + dlon]];
}

function hexPoints(lat, lon, r) {
    const pts = [];
    for (let i = 0; i < 6; i++) {
        const angle = i * 2 * Math.PI / 6;
        const dlat = r * Math.sin(angle) / 111111;
        const dlon = r * Math.cos(angle) / (111111 * Math.cos(lat * Math.PI / 180));
        pts.push([lat + dlat, lon + dlon]);
    }
    return pts;
}

function updateScale() {
    if (currentMode === "area" && currentPolygon) {
        // Approximate scale from polygon bounding box diagonal
        const lats = currentPolygon.map(c => c[1]);
        const lons = currentPolygon.map(c => c[0]);
        const latMid = (Math.min(...lats) + Math.max(...lats)) / 2;
        const dy = (Math.max(...lats) - Math.min(...lats)) * 111111;
        const dx = (Math.max(...lons) - Math.min(...lons)) * 111111 * Math.cos(latMid * Math.PI / 180);
        const diag = Math.sqrt(dx * dx + dy * dy);
        const s = Math.round(diag * 1000 / 190);
        scaleDisplay.textContent = s.toLocaleString();
        minFeature.textContent = Math.max(1, Math.round(diag / 2 / 300));
    } else {
        const r = parseInt(radiusSlider.value, 10);
        const s = Math.round(r * 2000 / 190);
        scaleDisplay.textContent = s.toLocaleString();
        minFeature.textContent = Math.max(1, Math.round(r / 300));
    }
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
    if (currentMode !== "pin" || currentLat === null) return;
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
    coordsLabel.textContent = "Selected location";
    coordsText.textContent = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    clearPolygon.classList.add("hidden");
    coordsDisplay.classList.remove("hidden");
    noPinMsg.classList.add("hidden");
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate terrain map";
    updateScale();
}

// ── Mode switching ─────────────────────────────────────────────────────────
function clearDrawnPolygon() {
    currentPolygon = null;
    drawnItems.clearLayers();
    coordsDisplay.classList.add("hidden");
    drawHint.classList.remove("hidden");
    generateBtn.disabled = true;
    generateBtn.textContent = "Draw an area first";
    updateScale();
    startDrawHandler();
}

function startDrawHandler() {
    if (drawHandler) { drawHandler.disable(); drawHandler = null; }
    drawHandler = new L.Draw.Polygon(map, {
        allowIntersection: false,
        showArea: false,
        shapeOptions: { color: "#3b82f6", fillOpacity: 0.1, weight: 2 },
        icon: new L.DivIcon({
            iconSize: new L.Point(8, 8),
            className: "leaflet-div-icon leaflet-editing-icon",
        }),
    });
    drawHandler.enable();
}

function enterPinMode() {
    currentMode = "pin";
    if (drawHandler) { drawHandler.disable(); drawHandler = null; }
    drawnItems.clearLayers();
    currentPolygon = null;
    pinOptions.classList.remove("hidden");
    drawHint.classList.add("hidden");
    noPinMsg.classList.toggle("hidden", currentLat !== null);
    coordsDisplay.classList.toggle("hidden", currentLat === null);
    clearPolygon.classList.add("hidden");
    if (currentLat !== null) {
        coordsLabel.textContent = "Selected location";
        coordsText.textContent = `${currentLat.toFixed(5)}, ${currentLon.toFixed(5)}`;
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
        refreshPreview();
    } else {
        generateBtn.disabled = true;
        generateBtn.textContent = "Set a location first";
    }
    updateScale();
}

function enterAreaMode() {
    currentMode = "area";
    currentLat = null;
    currentLon = null;
    if (pin) { map.removeLayer(pin); pin = null; }
    if (previewLayer) { map.removeLayer(previewLayer); previewLayer = null; }
    pinOptions.classList.add("hidden");
    noPinMsg.classList.add("hidden");
    coordsDisplay.classList.add("hidden");
    generateBtn.disabled = true;
    generateBtn.textContent = "Draw an area first";
    if (currentPolygon) {
        // Polygon already drawn — show it and re-enable generate
        drawHint.classList.add("hidden");
        coordsDisplay.classList.remove("hidden");
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
    } else {
        drawHint.classList.remove("hidden");
        startDrawHandler();
    }
    updateScale();
}

modeControl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    modeControl.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (btn.dataset.mode === "area") enterAreaMode();
    else enterPinMode();
});

// ── Polygon draw events ────────────────────────────────────────────────────
map.on(L.Draw.Event.CREATED, (e) => {
    drawnItems.clearLayers();
    drawnItems.addLayer(e.layer);
    const latlngs = e.layer.getLatLngs()[0];
    currentPolygon = latlngs.map(ll => [ll.lng, ll.lat]);
    drawHint.classList.add("hidden");
    coordsLabel.textContent = "Area";
    coordsText.textContent = `${currentPolygon.length} vertices`;
    clearPolygon.classList.remove("hidden");
    coordsDisplay.classList.remove("hidden");
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate terrain map";
    if (drawHandler) { drawHandler.disable(); drawHandler = null; }
    updateScale();
});

clearPolygon.addEventListener("click", (e) => {
    e.preventDefault();
    clearDrawnPolygon();
});

// ── Map click (pin mode only) ──────────────────────────────────────────────
map.on("click", (e) => {
    if (currentMode !== "pin") return;
    setPin(e.latlng.lat, e.latlng.lng);
});

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
        if (currentMode === "pin") setPin(llat, llon);
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
        previewBtn.classList.remove("hidden");
        previewBtn.onclick = () => window.openPreview && window.openPreview(jobId);
    } else {
        downloadBtn.classList.add("hidden");
        previewBtn.classList.add("hidden");
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
    if (currentMode === "pin" && currentLat == null) return;
    if (currentMode === "area" && !currentPolygon) return;
    stopPolling();
    generateBtn.disabled = true;
    generateBtn.textContent = "Submitting…";
    downloadBtn.classList.add("hidden");

    const contourVal = contourInput.value ? parseFloat(contourInput.value) : null;
    const bldgExagRaw = parseFloat(bldgExagSlider.value);
    const shared = {
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

    const body = currentMode === "area"
        ? { polygon: currentPolygon, ...shared }
        : { lat: currentLat, lon: currentLon, radius: parseInt(radiusSlider.value, 10), shape: currentShape, ...shared };

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
