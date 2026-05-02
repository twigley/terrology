# Terrology

Generate 3D-printable terrain and building models from OpenStreetMap and OpenTopography elevation data.

---

## Requirements

- [uv](https://docs.astral.sh/uv/)
- A free [OpenTopography API key](https://portal.opentopography.org/requestApiKey)

```bash
export OPENTOPO_API_KEY=your_key
```

---

## Usage

```bash
uv run main.py <location> [options]
```

### Modes

**Single location** — square map centred on a point:
```bash
uv run main.py "Canary Wharf, London" --radius 500
uv run main.py 51.5074,-0.1278 --radius 600 --scale 4000
```

**Two locations** — rectangular map spanning both points (each near an edge):
```bash
uv run main.py "51.5074,-0.1278" --to "51.5155,-0.0753"
uv run main.py "Edinburgh Castle" --to "Arthur's Seat, Edinburgh" --buffer 0.08
```

**Area (GeoJSON)** — map clipped to a polygon boundary; everything outside is void:
```bash
uv run main.py --area central_park.geojson
uv run main.py --area manhattan.geojson --dem-type SRTMGL1
```
Draw or export a polygon from [geojson.io](https://geojson.io), QGIS, or any GIS tool. The first polygon in the file is used. No location argument is needed — the polygon provides the extent.

**Route (GPX)** — terrain-only map with the GPX track painted as a coloured line:
```bash
uv run main.py --route my_ride.gpx
uv run main.py --route trail.gpx --route-width 2.0 --terrain-exag 3
```

---

## Output files

| File | Description |
|---|---|
| `terrain.stl` | Full terrain solid — use for mono-colour printing |
| `buildings.stl` | Building extrusions only |
| `water.stl` | Water surface patch (lakes, rivers, sea) |
| `parks.stl` | Parks/woodland surface patch |
| `roads.stl` | Roads/paths surface patch |
| `model.obj` + `model.mtl` | Combined coloured model for multi-colour slicers |

Import `model.obj` into Bambu Studio — it reads the MTL colours and lets you assign each material to a filament in the *Filament* panel.

The per-colour STLs (`water.stl`, `parks.stl`, `roads.stl`) are surface patches of the top layer only. Import them alongside `terrain.stl` into any slicer that handles multi-material via separate objects. Only files for colours present in the map are written.

### Materials

| Material | Used for |
|---|---|
| `terrain` | Terrain surface and buildings |
| `water` | Lakes, rivers, sea |
| `parks` | Parks, woodland, grassland |
| `roads` | Roads, paths |
| `route` | GPX route line (route mode only) |

Use `--colors` to limit the number of materials (e.g. `--colors 2` for a two-colour printer).

---

## Options

### Location / extent

| Flag | Default | Description |
|---|---|---|
| `location` | — | Place name or `"lat,lon"` — omit when using `--area` or `--route` |
| `--to` | — | Second location for a two-point map |
| `--area` | — | GeoJSON file whose first polygon defines the map boundary |
| `--route` | — | GPX file (route mode) |
| `--radius` | `500` | Radius in metres (single-point mode) |
| `--buffer` | `0.05` | Edge buffer as a fraction of span (two-point / area / route mode) |

### Model size & scale

| Flag | Default | Description |
|---|---|---|
| `--size` | `190` | Longest model dimension in mm |
| `--scale` | auto | Scale denominator — overrides `--size` (e.g. `3000` → 1:3000) |
| `--terrain-exag` | `2.0` | Vertical exaggeration of terrain height |

### Quality

| Flag | Default | Description |
|---|---|---|
| `--grid-size` | `200` | Terrain base mesh resolution N×N |
| `--color-grid-size` | `400` | Colour surface mesh resolution N×N — higher gives finer roads/paths |
| `--color-depth` | `1.5` | Depth (mm) colour features project into terrain — limits filament changes to surface layers |
| `--nozzle` | `0.4` | Nozzle diameter in mm. Both grid sizes are capped at `model_size ÷ (2 × nozzle)` so no cell is finer than the minimum printable feature. |
| `--dem-type` | `COP30` | Elevation dataset — see table below |

#### DEM types

| Dataset | Type | Resolution | Coverage | Best for |
|---|---|---|---|---|
| `COP30` *(default)* | DSM | 30 m | Global | Most areas — good detail, but includes building/tree heights in the surface |
| `SRTMGL1` | DSM | 30 m | Global (±60°) | Urban areas where building heights in COP30 distort the terrain — slightly smoother |
| `NASADEM` | DSM | 30 m | Global (±60°) | Similar to SRTMGL1; reprocessed SRTM with fewer voids |
| `AW3D30` | DSM | 30 m | Global | Often sharper than SRTM in mountainous areas |
| `EU_DTM` | **DTM** | 30 m | Europe only | Best terrain accuracy in Europe — bare-earth model that excludes buildings and trees |

**DSM vs DTM:** All datasets except `EU_DTM` are Digital *Surface* Models — they capture the top of whatever is on the ground, including buildings and forests. `EU_DTM` is a Digital *Terrain* Model (bare earth), so urban areas in Europe show flat streets rather than building spikes. For non-European cities, `SRTMGL1` tends to be less spiky than `COP30` in dense urban areas.

### Colour

| Flag | Default | Description |
|---|---|---|
| `--colors` | `4` | Number of materials — priority order: terrain+buildings, water, parks, roads. Route mode always uses 2 (terrain + route). |
| `--route-width` | `1.5` | Route line width on the printed model in mm |
| `--contour-interval` | — | Draw elevation contour lines every N real-world metres (e.g. `--contour-interval 50`). Uses a contrasting colour from the existing palette — no extra filament needed. |

### Misc

| Flag | Default | Description |
|---|---|---|
| `--output` | `./output` | Output directory |
| `--api-key` | env | OpenTopography API key (or set `OPENTOPO_API_KEY`) |
| `--no-terrain` | — | Skip terrain — buildings and features only |
| `--no-buildings` | — | Skip building extrusion — terrain and features only |
| `--no-cache` | — | Ignore cached downloads |
| `--smooth-boundary` | `0` | Smooth the `--area` polygon outline with N iterations of Chaikin corner-cutting (e.g. `3`–`5`). Rounds sharp corners between GeoJSON vertices. |

---

## Tips

**API key** — set once as an environment variable so you don't have to pass it every run:
```bash
export OPENTOPO_API_KEY=your_key
```

**Caching** — OSM and elevation data are cached in `~/.cache/3dmap/`. Re-runs with the same area are fast. Use `--no-cache` to force a fresh download. If features look unexpectedly missing, try `--no-cache` — a failed fetch is cached as empty.

**Area / polygon maps** — draw your boundary at [geojson.io](https://geojson.io) and save as a `.geojson` file. Useful for irregular shapes (a river valley, a city district, a national park) where a rectangular bbox would include unwanted terrain. Everything outside the polygon is removed entirely.
```bash
uv run main.py --area my_area.geojson --smooth-boundary 4
```

**Smooth polygon outlines** — if your GeoJSON polygon has few vertices, the map outline will have angular corners. Use `--smooth-boundary 3` to `5` to round them. More iterations pull the outline inward, so don't exceed ~6.

**Multi-colour printing** — import `model.obj` into Bambu Studio and assign each material name to a filament in the *Filament* panel. Buildings and terrain share the same material, so a 4-colour printer covers terrain, water, parks, and roads.

**Nozzle & triangle count** — the `--nozzle` default (0.4 mm) automatically caps grid resolutions so you never generate triangles the printer can't resolve. With a 0.6 mm nozzle the cap is tighter and files are smaller with no visible quality loss.

**Vertical exaggeration** — flat areas benefit from a higher `--terrain-exag` (try `3`–`5`). Mountainous areas may look better at `1.5`.

**Route maps** — the GPX bounding box is used automatically. Adjust `--buffer` to add more space around the track edges (default 5%). The route line width (`--route-width`) is in printed mm, not real-world metres, so it stays the same visual size regardless of map scale.

**Coastal maps** — coastlines and sea are detected automatically from OSM coastline data. Use `SRTMGL1` rather than `COP30` for dense coastal cities to avoid building spikes in the terrain.

**Contour lines** — `--contour-interval` paints elevation contours using a colour already in your palette — no extra filament required. Choose an interval that matches the relief: 10–25 m for gentle hills, 50–100 m for mountains. Contours are invisible in 1–2 colour mode.
```bash
uv run main.py "Zermatt, Switzerland" --radius 1500 --contour-interval 50
uv run main.py "Peak District" --radius 3000 --contour-interval 25 --terrain-exag 3
```

**Bridges** — road and railway segments tagged `bridge=yes` in OSM sit at the correct elevated position rather than being depressed into the hillside.

**Slicers without OBJ support** — import `terrain.stl`, `water.stl`, `parks.stl`, and `roads.stl` together and assign each a filament. Only files for features present in the map are written.
