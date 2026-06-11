"use strict";

// ── Map setup ──────────────────────────────────────────────────────────────
const map = L.map("map", { zoomControl: true }).setView([54, -2], 6);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© <a href='https://openstreetmap.org/copyright'>OpenStreetMap</a> contributors",
    maxZoom: 19,
}).addTo(map);

let pin = null;
let previewLayer = null;          // rectangle/circle/hex for pin mode
let currentLat = null;
let currentLon = null;
let currentShape = "square";
let currentMode = "pin";          // "pin" | "area" | "route"
let currentPolygon = null;        // [[lng, lat], ...] when drawn/imported
let drawHandler = null;
let clipDrawHandler = null;       // draw handler for route-mode clip polygon
const drawnItems = new L.FeatureGroup().addTo(map);

// Route state
let currentRoute = null;          // [[lon, lat], ...] parsed from GPX or null
let routeLayer = null;            // L.polyline for GPX preview
let routePreviewLayer = null;     // L.rectangle for two-point bbox preview
let routePointA = null;           // { lat, lon, marker }
let routePointB = null;           // { lat, lon, marker }
let routeClipPolygon = null;      // [[lng, lat], ...] optional clip in route mode

// Size/scale state
let scaleMode = "size";           // "size" | "scale"

// ── DOM refs ───────────────────────────────────────────────────────────────
const searchInput       = document.getElementById("search");
const searchBtn         = document.getElementById("search-btn");
const modeControl       = document.getElementById("mode-control");
const coordsDisplay     = document.getElementById("coords-display");
const coordsLabel       = document.getElementById("coords-label");
const coordsText        = document.getElementById("coords-text");
const clearPolygon      = document.getElementById("clear-polygon");
const noPinMsg          = document.getElementById("no-pin-msg");
const pinOptions        = document.getElementById("pin-options");
const areaOptions       = document.getElementById("area-options");
const routeOptions      = document.getElementById("route-options");
const radiusSlider      = document.getElementById("radius");
const radiusVal         = document.getElementById("radius-val");
const shapeControl      = document.getElementById("shape-control");
const drawHint          = document.getElementById("draw-hint");
const geojsonFile       = document.getElementById("geojson-file");
const smoothSlider      = document.getElementById("smooth-boundary");
const smoothVal         = document.getElementById("smooth-val");
const gpxFile           = document.getElementById("gpx-file");
const routeWidthSlider  = document.getElementById("route-width");
const routeWidthVal     = document.getElementById("route-width-val");
const spanBufferSlider  = document.getElementById("span-buffer");
const spanBufferVal     = document.getElementById("span-buffer-val");
const addClipPolyBtn    = document.getElementById("add-clip-polygon-btn");
const sizeScaleControl  = document.getElementById("size-scale-control");
const sizeInputWrap     = document.getElementById("size-input-wrap");
const scaleInputWrap    = document.getElementById("scale-input-wrap");
const sizeSlider        = document.getElementById("size-slider");
const sizeVal           = document.getElementById("size-val");
const scaleInput        = document.getElementById("scale-input");
const scaleSizeHint     = document.getElementById("scale-size-hint");
const exagSlider        = document.getElementById("exag");
const exagVal           = document.getElementById("exag-val");
const bldgExagSlider    = document.getElementById("bldg-exag");
const bldgExagVal       = document.getElementById("bldg-exag-val");
const colorsSelect      = document.getElementById("colors");
const buildingsBox      = document.getElementById("buildings");
const roofBox           = document.getElementById("roof-shapes");
const racewayBox        = document.getElementById("raceway");
const waterwaysBox      = document.getElementById("waterways");
const noTerrainBox      = document.getElementById("no-terrain");
const contourInput      = document.getElementById("contour");
const waterSlider       = document.getElementById("water-depth");
const waterVal          = document.getElementById("water-val");
const borderSlider      = document.getElementById("border-width");
const borderVal         = document.getElementById("border-val");
const demSelect         = document.getElementById("dem-source");
const demHint           = document.getElementById("dem-hint");
const racewayWidthSlider = document.getElementById("raceway-width");
const racewayWidthVal   = document.getElementById("raceway-width-val");
const waterwayWidthSlider = document.getElementById("waterway-width");
const waterwayWidthVal  = document.getElementById("waterway-width-val");
const colorDepthSlider  = document.getElementById("color-depth");
const colorDepthVal     = document.getElementById("color-depth-val");
const minBuildingArea   = document.getElementById("min-building-area");
const scaleDisplay      = document.getElementById("scale-display");
const minFeature        = document.getElementById("min-feature");
const generateBtn       = document.getElementById("generate-btn");
const previewBtn        = document.getElementById("preview-btn");
const statusArea        = document.getElementById("status-area");
const statusIcon        = document.getElementById("status-icon");
const statusMsg         = document.getElementById("status-msg");
const elapsedDiv        = document.getElementById("elapsed");
const downloadBtn       = document.getElementById("download-btn");

// ── GPX parsing ────────────────────────────────────────────────────────────
function parseGpxText(text) {
    let doc;
    try {
        doc = new DOMParser().parseFromString(text, "application/xml");
    } catch {
        alert("Failed to parse GPX file.");
        return null;
    }
    const parseErr = doc.querySelector("parsererror");
    if (parseErr) {
        alert("GPX file contains XML errors.");
        return null;
    }
    const root = doc.documentElement;

    function getPoints(tagLocal) {
        // Try namespaced and un-namespaced
        const pts = [];
        // getElementsByTagNameNS with "*" matches any ns
        const els = root.getElementsByTagNameNS("*", tagLocal);
        for (const el of els) {
            const lat = el.getAttribute("lat");
            const lon = el.getAttribute("lon");
            if (lat === null || lon === null) continue;
            const latF = parseFloat(lat);
            const lonF = parseFloat(lon);
            if (isNaN(latF) || isNaN(lonF)) continue;
            pts.push([lonF, latF]);
        }
        return pts;
    }

    let points = getPoints("trkpt");
    if (!points.length) {
        points = getPoints("rtept");
    }
    if (!points.length) {
        alert("No track or route points found in the GPX file.");
        return null;
    }

    // Downsample to <=5000 points, always keeping the last
    if (points.length > 5000) {
        const step = (points.length - 1) / 4999;
        const sampled = [];
        for (let i = 0; i < 4999; i++) {
            sampled.push(points[Math.round(i * step)]);
        }
        sampled.push(points[points.length - 1]);
        points = sampled;
    }
    return points;
}

// ── GeoJSON import ─────────────────────────────────────────────────────────
function parseGeoJsonText(text) {
    let geojson;
    try {
        geojson = JSON.parse(text);
    } catch {
        alert("Failed to parse GeoJSON — invalid JSON.");
        return;
    }

    let geometry = null;
    if (geojson.type === "FeatureCollection") {
        const feat = geojson.features.find(f =>
            f.geometry &&
            (f.geometry.type === "Polygon" || f.geometry.type === "MultiPolygon")
        );
        if (!feat) { alert("No Polygon or MultiPolygon feature found in FeatureCollection."); return; }
        geometry = feat.geometry;
    } else if (geojson.type === "Feature") {
        geometry = geojson.geometry;
    } else {
        geometry = geojson;
    }

    if (!geometry) { alert("Could not read geometry from GeoJSON."); return; }

    let ring = null;
    if (geometry.type === "Polygon") {
        ring = geometry.coordinates[0]; // exterior ring, drop holes
    } else if (geometry.type === "MultiPolygon") {
        ring = geometry.coordinates[0][0]; // first polygon's exterior ring
    } else {
        alert(`Unsupported geometry type "${geometry.type}" — only Polygon and MultiPolygon are supported.`);
        return;
    }

    // ring is [[lng, lat], ...] in GeoJSON
    currentPolygon = ring.map(c => [c[0], c[1]]);

    // Draw on map
    drawnItems.clearLayers();
    const latLngs = currentPolygon.map(c => [c[1], c[0]]);
    const layer = L.polygon(latLngs, { color: "#3b82f6", fillOpacity: 0.1, weight: 2 });
    drawnItems.addLayer(layer);
    map.fitBounds(layer.getBounds());

    drawHint.classList.add("hidden");
    coordsLabel.textContent = "Area";
    coordsText.textContent = `${currentPolygon.length} vertices (imported)`;
    clearPolygon.classList.remove("hidden");
    coordsDisplay.classList.remove("hidden");
    generateBtn.disabled = false;
    generateBtn.textContent = "Generate terrain map";
    if (drawHandler) { drawHandler.disable(); drawHandler = null; }
    updateScale();
}

// ── Route helpers ──────────────────────────────────────────────────────────
function clearRouteState() {
    currentRoute = null;
    if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    clearRoutePreview();
    clearTwoPointMarkers();
    routeClipPolygon = null;
    drawnItems.clearLayers();
}

function clearTwoPointMarkers() {
    if (routePointA) { map.removeLayer(routePointA.marker); routePointA = null; }
    if (routePointB) { map.removeLayer(routePointB.marker); routePointB = null; }
    clearRoutePreview();
}

function clearRoutePreview() {
    if (routePreviewLayer) { map.removeLayer(routePreviewLayer); routePreviewLayer = null; }
}

function refreshRoutePreview() {
    clearRoutePreview();
    const buf = parseFloat(spanBufferSlider.value);

    if (currentRoute && currentRoute.length >= 2) {
        // GPX: draw polyline
        if (routeLayer) map.removeLayer(routeLayer);
        const latLngs = currentRoute.map(c => [c[1], c[0]]);
        routeLayer = L.polyline(latLngs, { color: "#D23723", weight: 3, opacity: 0.8 }).addTo(map);

        // Also show buffered bbox
        const lons = currentRoute.map(c => c[0]);
        const lats = currentRoute.map(c => c[1]);
        const minLon = Math.min(...lons), maxLon = Math.max(...lons);
        const minLat = Math.min(...lats), maxLat = Math.max(...lats);
        const dLon = (maxLon - minLon) * buf;
        const dLat = (maxLat - minLat) * buf;
        const sw = [minLat - dLat, minLon - dLon];
        const ne = [maxLat + dLat, maxLon + dLon];
        routePreviewLayer = L.rectangle([sw, ne], { color: "#3b82f6", fillOpacity: 0.06, weight: 1.5, dashArray: "4 4" }).addTo(map);
        return;
    }

    if (routePointA && routePointB) {
        // Two-point: bbox rectangle
        const lons = [routePointA.lon, routePointB.lon];
        const lats = [routePointA.lat, routePointB.lat];
        const minLon = Math.min(...lons), maxLon = Math.max(...lons);
        const minLat = Math.min(...lats), maxLat = Math.max(...lats);
        const dLon = (maxLon - minLon) * buf;
        const dLat = (maxLat - minLat) * buf;
        const sw = [minLat - dLat, minLon - dLon];
        const ne = [maxLat + dLat, maxLon + dLon];
        routePreviewLayer = L.rectangle([sw, ne], { color: "#3b82f6", fillOpacity: 0.08, weight: 2 }).addTo(map);
    }
}

function routeSpanM() {
    const buf = parseFloat(spanBufferSlider.value);
    let lons, lats;
    if (currentRoute && currentRoute.length >= 2) {
        lons = currentRoute.map(c => c[0]);
        lats = currentRoute.map(c => c[1]);
    } else if (routePointA && routePointB) {
        lons = [routePointA.lon, routePointB.lon];
        lats = [routePointA.lat, routePointB.lat];
    } else {
        return null;
    }
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const latMid = (minLat + maxLat) / 2;
    const dy = (maxLat - minLat) * (1 + 2 * buf) * 111111;
    const dx = (maxLon - minLon) * (1 + 2 * buf) * 111111 * Math.cos(latMid * Math.PI / 180);
    return Math.max(dx, dy);
}

// 300 km span check (server limit)
function routeSpanTooLarge() {
    const span = routeSpanM();
    return span !== null && span > 300000;
}

// ── Scale display ──────────────────────────────────────────────────────────
function updateScale() {
    let spanM = null;
    if (currentMode === "pin") {
        spanM = parseInt(radiusSlider.value, 10) * 2;
    } else if (currentMode === "area" && currentPolygon) {
        const lats = currentPolygon.map(c => c[1]);
        const lons = currentPolygon.map(c => c[0]);
        const latMid = (Math.min(...lats) + Math.max(...lats)) / 2;
        const dy = (Math.max(...lats) - Math.min(...lats)) * 111111;
        const dx = (Math.max(...lons) - Math.min(...lons)) * 111111 * Math.cos(latMid * Math.PI / 180);
        spanM = Math.sqrt(dx * dx + dy * dy);
    } else if (currentMode === "route") {
        spanM = routeSpanM();
    }

    if (spanM === null) {
        scaleDisplay.textContent = "—";
        minFeature.textContent = "—";
        return;
    }

    const sizeMm = parseInt(sizeSlider.value, 10);
    let computedScale;
    if (scaleMode === "scale") {
        computedScale = parseInt(scaleInput.value, 10) || 25000;
        const modelSizeMm = (spanM * 1000) / computedScale;
        scaleSizeHint.textContent = `Model size ≈ ${modelSizeMm.toFixed(0)} mm`;
        if (modelSizeMm > 256) {
            scaleSizeHint.classList.add("scale-size-warn");
        } else {
            scaleSizeHint.classList.remove("scale-size-warn");
        }
    } else {
        computedScale = Math.round((spanM * 1000) / sizeMm);
    }

    scaleDisplay.textContent = computedScale.toLocaleString();
    // min printable feature: span / 800
    minFeature.textContent = Math.max(1, Math.round(spanM / 800));
}

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
    if (clipDrawHandler) { clipDrawHandler.disable(); clipDrawHandler = null; }
    clearRouteState();
    pinOptions.classList.remove("hidden");
    areaOptions.classList.add("hidden");
    routeOptions.classList.add("hidden");
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
    if (clipDrawHandler) { clipDrawHandler.disable(); clipDrawHandler = null; }
    clearRouteState();
    pinOptions.classList.add("hidden");
    areaOptions.classList.remove("hidden");
    routeOptions.classList.add("hidden");
    noPinMsg.classList.add("hidden");
    if (currentPolygon) {
        drawHint.classList.add("hidden");
        coordsDisplay.classList.remove("hidden");
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
    } else {
        coordsDisplay.classList.add("hidden");
        generateBtn.disabled = true;
        generateBtn.textContent = "Draw an area first";
        drawHint.classList.remove("hidden");
        startDrawHandler();
    }
    updateScale();
}

function enterRouteMode() {
    currentMode = "route";
    currentLat = null;
    currentLon = null;
    currentPolygon = null;
    if (pin) { map.removeLayer(pin); pin = null; }
    if (previewLayer) { map.removeLayer(previewLayer); previewLayer = null; }
    if (drawHandler) { drawHandler.disable(); drawHandler = null; }
    pinOptions.classList.add("hidden");
    areaOptions.classList.add("hidden");
    routeOptions.classList.remove("hidden");
    noPinMsg.classList.add("hidden");
    coordsDisplay.classList.add("hidden");
    // Restore any existing route state
    if (currentRoute && currentRoute.length >= 2) {
        coordsLabel.textContent = "Route";
        coordsText.textContent = `${currentRoute.length} points`;
        clearPolygon.classList.remove("hidden");
        coordsDisplay.classList.remove("hidden");
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
        refreshRoutePreview();
    } else if (routePointA && routePointB) {
        coordsLabel.textContent = "Route";
        coordsText.textContent = "Two points selected";
        clearPolygon.classList.remove("hidden");
        coordsDisplay.classList.remove("hidden");
        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
        refreshRoutePreview();
    } else {
        generateBtn.disabled = true;
        generateBtn.textContent = "Load GPX or pick two points";
    }
    updateScale();
}

modeControl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    modeControl.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const mode = btn.dataset.mode;
    if (mode === "area") enterAreaMode();
    else if (mode === "route") enterRouteMode();
    else enterPinMode();
});

// ── Polygon draw events (area + route clip) ────────────────────────────────
map.on(L.Draw.Event.CREATED, (e) => {
    if (currentMode === "route" && clipDrawHandler) {
        // This is a route clip polygon
        clipDrawHandler.disable();
        clipDrawHandler = null;
        addClipPolyBtn.textContent = "Add clip polygon";
        const latlngs = e.layer.getLatLngs()[0];
        routeClipPolygon = latlngs.map(ll => [ll.lng, ll.lat]);
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);
        return;
    }

    // Normal area draw
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
    if (currentMode === "area") {
        clearDrawnPolygon();
    } else if (currentMode === "route") {
        clearRouteState();
        gpxFile.value = "";
        coordsDisplay.classList.add("hidden");
        generateBtn.disabled = true;
        generateBtn.textContent = "Load GPX or pick two points";
        updateScale();
    }
});

// ── Map click ──────────────────────────────────────────────────────────────
map.on("click", (e) => {
    if (currentMode === "pin") {
        setPin(e.latlng.lat, e.latlng.lng);
        return;
    }
    if (currentMode === "route" && !currentRoute) {
        // Two-point mode: place A then B
        if (!routePointA) {
            const marker = L.marker([e.latlng.lat, e.latlng.lng], {
                icon: L.divIcon({ html: "<b style='color:#3b82f6;font-size:1rem'>A</b>", className: "route-point-icon", iconSize: [16, 16] })
            }).addTo(map);
            routePointA = { lat: e.latlng.lat, lon: e.latlng.lng, marker };
        } else if (!routePointB) {
            const marker = L.marker([e.latlng.lat, e.latlng.lng], {
                icon: L.divIcon({ html: "<b style='color:#D23723;font-size:1rem'>B</b>", className: "route-point-icon", iconSize: [16, 16] })
            }).addTo(map);
            routePointB = { lat: e.latlng.lat, lon: e.latlng.lng, marker };

            coordsLabel.textContent = "Route";
            coordsText.textContent = "Two points selected";
            clearPolygon.classList.remove("hidden");
            coordsDisplay.classList.remove("hidden");
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate terrain map";
            refreshRoutePreview();
            updateScale();
        }
    }
});

// ── GPX file input ─────────────────────────────────────────────────────────
gpxFile.addEventListener("change", () => {
    const file = gpxFile.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
        const points = parseGpxText(ev.target.result);
        if (!points) { gpxFile.value = ""; return; }
        currentRoute = points;
        // Clear two-point markers since GPX takes precedence
        clearTwoPointMarkers();

        coordsLabel.textContent = "Route";
        coordsText.textContent = `${points.length} points (GPX)`;
        clearPolygon.classList.remove("hidden");
        coordsDisplay.classList.remove("hidden");

        if (routeSpanTooLarge()) {
            alert("Route span exceeds 300 km — please use a shorter section.");
            clearRouteState();
            gpxFile.value = "";
            coordsDisplay.classList.add("hidden");
            generateBtn.disabled = true;
            generateBtn.textContent = "Load GPX or pick two points";
            return;
        }

        generateBtn.disabled = false;
        generateBtn.textContent = "Generate terrain map";
        refreshRoutePreview();
        // Fit map to route
        if (routeLayer) map.fitBounds(routeLayer.getBounds(), { padding: [20, 20] });
        updateScale();
    };
    reader.readAsText(file);
});

// ── GeoJSON file input ─────────────────────────────────────────────────────
geojsonFile.addEventListener("change", () => {
    const file = geojsonFile.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
        parseGeoJsonText(ev.target.result);
    };
    reader.readAsText(file);
});

// ── Add clip polygon (route mode) ─────────────────────────────────────────
addClipPolyBtn.addEventListener("click", () => {
    if (clipDrawHandler) {
        clipDrawHandler.disable();
        clipDrawHandler = null;
        addClipPolyBtn.textContent = "Add clip polygon";
        return;
    }
    clipDrawHandler = new L.Draw.Polygon(map, {
        allowIntersection: false,
        showArea: false,
        shapeOptions: { color: "#f59e0b", fillOpacity: 0.1, weight: 2 },
        icon: new L.DivIcon({
            iconSize: new L.Point(8, 8),
            className: "leaflet-div-icon leaflet-editing-icon",
        }),
    });
    clipDrawHandler.enable();
    addClipPolyBtn.textContent = "Cancel clip polygon";
});

// ── Size / scale toggle ────────────────────────────────────────────────────
sizeScaleControl.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    sizeScaleControl.querySelectorAll(".seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    scaleMode = btn.dataset.sizemode;
    if (scaleMode === "scale") {
        sizeInputWrap.classList.add("hidden");
        scaleInputWrap.classList.remove("hidden");
    } else {
        sizeInputWrap.classList.remove("hidden");
        scaleInputWrap.classList.add("hidden");
        scaleSizeHint.textContent = "";
        scaleSizeHint.classList.remove("scale-size-warn");
    }
    updateScale();
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

smoothSlider.addEventListener("input", () => {
    smoothVal.textContent = parseInt(smoothSlider.value, 10);
});

routeWidthSlider.addEventListener("input", () => {
    routeWidthVal.textContent = parseFloat(routeWidthSlider.value).toFixed(1);
});

spanBufferSlider.addEventListener("input", () => {
    spanBufferVal.textContent = parseFloat(spanBufferSlider.value).toFixed(2);
    refreshRoutePreview();
    updateScale();
});

sizeSlider.addEventListener("input", () => {
    sizeVal.textContent = parseInt(sizeSlider.value, 10);
    updateScale();
});

scaleInput.addEventListener("input", () => {
    updateScale();
});

racewayWidthSlider.addEventListener("input", () => {
    racewayWidthVal.textContent = parseFloat(racewayWidthSlider.value).toFixed(1);
});

waterwayWidthSlider.addEventListener("input", () => {
    waterwayWidthVal.textContent = parseFloat(waterwayWidthSlider.value).toFixed(1);
});

colorDepthSlider.addEventListener("input", () => {
    colorDepthVal.textContent = parseFloat(colorDepthSlider.value).toFixed(1);
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

// ── no_terrain checkbox guard ──────────────────────────────────────────────
noTerrainBox.addEventListener("change", () => {
    if (noTerrainBox.checked) {
        buildingsBox.checked = true;
        buildingsBox.disabled = true;
    } else {
        buildingsBox.disabled = false;
    }
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
    // Guard: mode-specific readiness
    if (currentMode === "pin" && currentLat == null) return;
    if (currentMode === "area" && !currentPolygon) return;
    if (currentMode === "route" && !currentRoute && !(routePointA && routePointB)) return;

    // Guard: span limit
    if (currentMode === "route" && routeSpanTooLarge()) {
        alert("Route span exceeds 300 km — please use a shorter route or reduce the span buffer.");
        return;
    }

    // Guard: no_terrain + route
    if (noTerrainBox.checked && currentMode === "route") {
        alert("Skip terrain cannot be combined with Route mode.");
        return;
    }

    stopPolling();
    generateBtn.disabled = true;
    generateBtn.textContent = "Submitting…";
    downloadBtn.classList.add("hidden");

    const contourVal = contourInput.value ? parseFloat(contourInput.value) : null;
    const bldgExagRaw = parseFloat(bldgExagSlider.value);
    const minBldgVal = minBuildingArea.value !== "" ? parseFloat(minBuildingArea.value) : null;

    // Shared params common to all modes
    const shared = {
        terrain_exag: parseFloat(exagSlider.value),
        colors: parseInt(colorsSelect.value, 10),
        no_buildings: !buildingsBox.checked,
        roof_shapes: roofBox.checked,
        raceway: racewayBox.checked,
        waterways: waterwaysBox.checked,
        contour_interval: contourVal,
        building_exag: bldgExagRaw > 0 ? bldgExagRaw : null,
        water_depth_mm: parseFloat(waterSlider.value),
        border_width_mm: parseInt(borderSlider.value, 10),
        dem_source: demSelect.value,
        raceway_width: parseFloat(racewayWidthSlider.value),
        waterway_width: parseFloat(waterwayWidthSlider.value),
        color_depth_mm: parseFloat(colorDepthSlider.value),
        min_building_area: minBldgVal,
        no_terrain: noTerrainBox.checked,
    };

    // Size or scale — send only the active one
    if (scaleMode === "scale") {
        shared.scale = parseInt(scaleInput.value, 10) || null;
    } else {
        shared.size = parseInt(sizeSlider.value, 10);
    }

    let body;
    if (currentMode === "route" && currentRoute) {
        // GPX route mode — optionally with a clip polygon
        body = {
            route: currentRoute,
            route_width: parseFloat(routeWidthSlider.value),
            span_buffer: parseFloat(spanBufferSlider.value),
            ...(routeClipPolygon ? {
                polygon: routeClipPolygon,
                smooth_boundary: parseInt(smoothSlider.value, 10),
            } : {}),
            ...shared,
        };
    } else if (currentMode === "route" && routePointA && routePointB) {
        // Two-point route mode
        body = {
            lat: routePointA.lat,
            lon: routePointA.lon,
            to_lat: routePointB.lat,
            to_lon: routePointB.lon,
            span_buffer: parseFloat(spanBufferSlider.value),
            ...shared,
        };
    } else if (currentMode === "area") {
        body = {
            polygon: currentPolygon,
            smooth_boundary: parseInt(smoothSlider.value, 10),
            ...shared,
        };
    } else {
        // Pin mode
        body = {
            lat: currentLat,
            lon: currentLon,
            radius: parseInt(radiusSlider.value, 10),
            shape: currentShape,
            ...shared,
        };
    }

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
        if (res.status === 503) {
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate terrain map";
            showStatus("⏳", "Server busy", "Try again shortly.");
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
