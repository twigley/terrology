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
| `terrology/fetcher.py` | OSM + OpenTopography downloads, caching |
| `terrology/exporter.py` | STL, OBJ+MTL output |
| `terrology/cache.py` | Disk cache under `~/.cache/3dmap/` |
| `docs/bambu_3mf_format.md` | Reverse-engineered Bambu Studio 3MF spec |

## Primary output

`model.obj` + `model.mtl` — import into Bambu Studio, assign each material to a filament slot.

3MF export was removed: the `paint_color` per-triangle encoding works correctly but
`project_settings.config` causes unavoidable "customised presets" dialogs and slot-naming
issues without knowing the user's installed Bambu preset names. See `docs/bambu_3mf_format.md`.

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

## Known issues / gotchas

- Stale cache: a failed OSM layer fetch is cached as empty. Use `--no-cache` to re-fetch.
- Water missing in `--area` mode: reservoir/lake may be outside the drawn polygon.
- `--smooth-boundary` applies Chaikin corner-cutting to the GeoJSON polygon before clipping —
  each iteration pulls vertices slightly inward so don't exceed ~6.
