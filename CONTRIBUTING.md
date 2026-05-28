# Contributing

## Setup

```bash
git clone <repo>
cd terrology
uv sync --extra web
```

## Running the tool

```bash
uv run main.py "Snowdon" --radius 500   # in the repo
terrology "Snowdon" --radius 500        # when installed
```

## Running the web UI

```bash
uv run uvicorn web.app:app --reload
```

## Tests and linting

Run these before every commit:

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

Write tests alongside new code, not after.

## Key files

| File | Purpose |
|---|---|
| `main.py` | CLI entry point — argument parsing, orchestration |
| `terrology/builder.py` | `MapBuilder` — terrain mesh, buildings, colouring, clipping |
| `terrology/fetcher.py` | OSM + elevation downloads, caching |
| `terrology/decorations.py` | Border frame mesh |
| `terrology/exporter.py` | STL, OBJ+MTL, 3MF output |
| `terrology/cache.py` | Disk cache under `~/.cache/3dmap/` |
| `web/` | FastAPI web interface |

## Notes

- Always use `uv`. Don't use pip directly.
- The cache stores failed fetches as empty results — use `--no-cache` when debugging missing features.
- Stale cache is the most common cause of unexpected empty output.
