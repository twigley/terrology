from __future__ import annotations

import json
import math
import multiprocessing
import os
from datetime import UTC, datetime
from pathlib import Path

from web.jobs import JobStatus, store

_JOB_DIR = Path(os.environ.get("TERROLOGY_JOB_DIR", "/tmp/terrology"))


def _make_shape_polygon(lat: float, lon: float, radius: float, shape: str):
    """Return a WGS84 shapely Polygon for the requested shape centred at lat/lon."""
    from pyproj import CRS, Transformer
    from shapely.geometry import Point, Polygon
    from shapely.ops import transform as _shp_transform

    from terrology.builder import _utm_crs

    utm_crs = _utm_crs(lon, lat)
    wgs84 = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    from_utm = Transformer.from_crs(utm_crs, wgs84, always_xy=True)

    cx, cy = to_utm.transform(lon, lat)

    if shape == "circle":
        poly_utm = Point(cx, cy).buffer(radius, resolution=64)
    elif shape == "hexagon":
        angles = [i * 2 * math.pi / 6 for i in range(6)]  # flat top + bottom
        points = [
            (cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in angles
        ]
        poly_utm = Polygon(points)
    else:
        raise ValueError(f"Unknown shape: {shape!r}")

    return _shp_transform(lambda x, y: from_utm.transform(x, y), poly_utm)


def _worker(params_json: str, out_dir_str: str) -> None:
    """Runs in a spawned child process; writes _status.json on completion."""
    out_dir = Path(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    status_file = out_dir / "_status.json"

    try:
        params = json.loads(params_json)
        from terrology.cli import run_pipeline

        clip_polygon_wgs84 = None
        polygon_coords = params.get("polygon")
        if polygon_coords is not None:
            from shapely.geometry import Polygon as _Polygon

            clip_polygon_wgs84 = _Polygon([(c[0], c[1]) for c in polygon_coords])
            centroid = clip_polygon_wgs84.centroid
            lat = centroid.y
            lon = centroid.x
            radius = params.get("radius", 500)
        else:
            lat = params["lat"]
            lon = params["lon"]
            radius = params["radius"]
            shape = params.get("shape", "square")
            if shape != "square":
                clip_polygon_wgs84 = _make_shape_polygon(lat, lon, radius, shape)

        run_pipeline(
            lat=lat,
            lon=lon,
            radius=radius,
            clip_polygon_wgs84=clip_polygon_wgs84,
            terrain_exag=params["terrain_exag"],
            colors=params["colors"],
            no_buildings=params.get("no_buildings", False),
            roof_shapes=params.get("roof_shapes", False),
            contour_interval=params.get("contour_interval"),
            border_width_mm=params.get("border_width_mm", 0.0),
            water_depth_mm=params.get("water_depth_mm", 0.8),
            building_exag=params.get("building_exag"),
            dem_source=params.get("dem_source", "glo30"),
            output_dir=out_dir,
            color_grid_size=800,
            skip_stls=True,
        )
        status_file.write_text(json.dumps({"status": "ready"}))
    except Exception as exc:
        status_file.write_text(json.dumps({"status": "error", "error": str(exc)}))


def run_job(job_id: str, params: dict) -> None:
    """Called by FastAPI BackgroundTasks; runs run_pipeline in a spawned child process."""
    out_dir = _JOB_DIR / job_id
    store.update(
        job_id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(tz=UTC),
        output_dir=out_dir,
    )

    # Spawn a fresh process so all pipeline memory is released when the job ends.
    # "spawn" avoids inheriting asyncio state and thread locks from the server process.
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=_worker, args=(json.dumps(params), str(out_dir)))
    p.start()
    p.join()

    status_file = out_dir / "_status.json"
    if status_file.exists():
        data = json.loads(status_file.read_text())
        if data.get("status") == "ready":
            store.update(job_id, status=JobStatus.READY)
        else:
            store.update(
                job_id,
                status=JobStatus.ERROR,
                error=data.get("error", "Unknown error"),
            )
    else:
        store.update(
            job_id, status=JobStatus.ERROR, error="Job process exited unexpectedly"
        )
