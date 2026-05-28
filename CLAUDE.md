# Terrology — Claude Code notes

## Running the project

```bash
uv run main.py <args>
```

Always use `uv`. Never pip-install or suggest requirements.txt.

## Key source files

| File | Purpose |
|---|---|
| `main.py` | CLI entry point — argument parsing, orchestration |
| `terrology/builder.py` | `MapBuilder` — terrain mesh, buildings, colouring, clipping |
| `terrology/fetcher.py` | OSM + elevation downloads, caching |
| `terrology/decorations.py` | Border frame mesh (`make_frame_mesh`) |
| `terrology/exporter.py` | STL, OBJ+MTL output |
| `terrology/cache.py` | Disk cache under `~/.cache/3dmap/` |
| `docs/bambu_3mf_format.md` | Reverse-engineered Bambu Studio 3MF spec |

## Primary output

`model.obj` + `model.mtl` — import into Bambu Studio, assign each material to a filament slot.

`model.3mf` is also exported. The per-face colour encoding works correctly, but Bambu Studio
may show "customised presets" dialogs if `project_settings.config` references preset names that
don't match the user's installed Bambu presets. See `docs/bambu_3mf_format.md`.

## Materials / colours

Defined in `exporter.py` (`COLOUR_NAMES`, `_COLOURS_RGB`). OBJ uses these as named MTL materials.

| Material | Colour | Used for |
|---|---|---|
| terrain | #B4A082 stone/sand | Terrain base + buildings |
| water | #468CD2 blue | Lakes, rivers, sea |
| parks | #50A550 green | Parks, woodland |
| roads | #DCD7C8 light grey | Roads, paths |
| route | #D23723 red | GPX route line (route mode only) |

## Architecture notes

- Terrain is split into `terrain_base_mesh` (bulk) and `terrain_surface_mesh` (thin top layer).
  Colour features only appear in the surface layer — limits filament changes to top layers.
- `--area` mode clips all three meshes (terrain, base, surface) to the polygon via boolean
  intersection using manifold. The clip polygon is in UTM mm coordinates.
- OSM features are fetched by bbox, not clipped — colouring uses face-centroid containment tests.
- If features look missing, try `--no-cache` — the cache stores empty results on fetch errors.

## Quality checks

After any code change, always run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

Write tests before or alongside new code, not after.

## Elevation sources

GLO-30 is the default — fetched directly from the Copernicus S3 bucket, no API key needed.
AW3D30 and SRTM use the OpenTopography API and require a free key stored in
`~/.config/terrology/config`:

```
OPENTOPOGRAPHY_API_KEY=<your-key>
```

Or save it once with: `uv run main.py --save-api-key <key>`

**AW3D30 gives better bridge deck elevations** — GLO-30 (radar) sometimes records bridge
decks at river-surface level; AW3D30 (optical stereo) measures the visible deck surface.
Use `--dem aw3d30` when bridge accuracy matters.

## Known issues / gotchas

- Stale cache: a failed OSM layer fetch is cached as empty. Use `--no-cache` to re-fetch.
- Water missing in `--area` mode: reservoir/lake may be outside the drawn polygon.
- `--smooth-boundary` applies Chaikin corner-cutting to the GeoJSON polygon before clipping —
  each iteration pulls vertices slightly inward so don't exceed ~6.
- Bridge deck elevation: GLO-30 records some bridges at river level (radar sees water through
  deck gaps). Use `--dem aw3d30` to fix this — confirmed better for UK road bridges.
