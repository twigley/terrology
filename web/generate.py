from __future__ import annotations

import json
import multiprocessing
import os
from datetime import UTC, datetime
from pathlib import Path

from terrology.cli import (
    _bbox_with_buffer,
    _chaikin_smooth,
    _setup_utm,
)
from terrology.cli import (
    make_shape_polygon as _make_shape_polygon,
)
from web.jobs import JobStatus, store

_JOB_DIR = Path(os.environ.get("TERROLOGY_JOB_DIR", "/tmp/terrology"))


def _worker(params_json: str, out_dir_str: str) -> None:
    """Runs in a spawned child process; writes _status.json on completion."""
    out_dir = Path(out_dir_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    status_file = out_dir / "_status.json"

    try:
        params = json.loads(params_json)
        from terrology.cli import run_pipeline

        clip_polygon_wgs84 = None
        clip_polygon_utm = None
        bbox_utm = None
        route_points_utm = None

        polygon_coords = params.get("polygon")
        route_coords = params.get("route")  # [[lon, lat], ...]
        to_lat = params.get("to_lat")
        span_buffer = params.get("span_buffer", 0.05)

        if polygon_coords is not None:
            from shapely.geometry import Polygon as _Polygon
            from shapely.ops import transform as _shp_transform

            clip_polygon_wgs84 = _Polygon([(c[0], c[1]) for c in polygon_coords])

            # Apply Chaikin smoothing in WGS84 space (matches CLI behaviour)
            smooth_n = params.get("smooth_boundary", 0)
            if smooth_n > 0:
                clip_polygon_wgs84 = _chaikin_smooth(clip_polygon_wgs84, smooth_n)

            if route_coords is not None:
                # Area + route mode
                bnds = clip_polygon_wgs84.bounds  # (min_lon, min_lat, max_lon, max_lat)
                lat = (bnds[1] + bnds[3]) / 2
                lon = (bnds[0] + bnds[2]) / 2
                _, to_utm, _ = _setup_utm(lat, lon)

                clip_polygon_utm = _shp_transform(
                    lambda x, y: to_utm.transform(x, y), clip_polygon_wgs84
                )
                ab = clip_polygon_utm.bounds  # (x_min, y_min, x_max, y_max)
                bbox_utm = _bbox_with_buffer(
                    [ab[0], ab[2]], [ab[1], ab[3]], span_buffer
                )
                route_points_utm = [
                    to_utm.transform(rlon, rlat) for rlon, rlat in route_coords
                ]
                run_pipeline(
                    lat=lat,
                    lon=lon,
                    bbox_utm=bbox_utm,
                    clip_polygon_utm=clip_polygon_utm,
                    terrain_exag=params["terrain_exag"],
                    colors=params["colors"],
                    no_buildings=params.get("no_buildings", False),
                    no_terrain=params.get("no_terrain", False),
                    roof_shapes=params.get("roof_shapes", False),
                    contour_interval=params.get("contour_interval"),
                    border_width_mm=params.get("border_width_mm", 0.0),
                    water_depth_mm=params.get("water_depth_mm", 0.8),
                    building_exag=params.get("building_exag"),
                    dem_source=params.get("dem_source", "glo30"),
                    raceway=params.get("raceway", False),
                    raceway_width=params.get("raceway_width", 1.5),
                    waterways=params.get("waterways", False),
                    waterway_width=params.get("waterway_width", 1.0),
                    route_points_utm=route_points_utm,
                    route_width=params.get("route_width", 1.5),
                    scale=params.get("scale"),
                    size=params.get("size", 190.0),
                    color_depth_mm=params.get("color_depth_mm", 1.5),
                    min_building_area=params.get("min_building_area"),
                    output_dir=out_dir,
                    color_grid_size=800,
                    skip_stls=True,
                )
            else:
                # Polygon-only mode: keep existing call path exactly for byte-identical output
                centroid = clip_polygon_wgs84.centroid
                lat = centroid.y
                lon = centroid.x
                radius = params.get("radius", 500)
                run_pipeline(
                    lat=lat,
                    lon=lon,
                    radius=radius,
                    clip_polygon_wgs84=clip_polygon_wgs84,
                    terrain_exag=params["terrain_exag"],
                    colors=params["colors"],
                    no_buildings=params.get("no_buildings", False),
                    no_terrain=params.get("no_terrain", False),
                    roof_shapes=params.get("roof_shapes", False),
                    contour_interval=params.get("contour_interval"),
                    border_width_mm=params.get("border_width_mm", 0.0),
                    water_depth_mm=params.get("water_depth_mm", 0.8),
                    building_exag=params.get("building_exag"),
                    dem_source=params.get("dem_source", "glo30"),
                    raceway=params.get("raceway", False),
                    raceway_width=params.get("raceway_width", 1.5),
                    waterways=params.get("waterways", False),
                    waterway_width=params.get("waterway_width", 1.0),
                    scale=params.get("scale"),
                    size=params.get("size", 190.0),
                    color_depth_mm=params.get("color_depth_mm", 1.5),
                    min_building_area=params.get("min_building_area"),
                    output_dir=out_dir,
                    color_grid_size=800,
                    skip_stls=True,
                )

        elif route_coords is not None:
            # Route-only mode
            lons = [pt[0] for pt in route_coords]
            lats = [pt[1] for pt in route_coords]
            lat = (min(lats) + max(lats)) / 2
            lon = (min(lons) + max(lons)) / 2
            _, to_utm, _ = _setup_utm(lat, lon)

            route_points_utm = [
                to_utm.transform(rlon, rlat) for rlon, rlat in route_coords
            ]
            xs = [p[0] for p in route_points_utm]
            ys = [p[1] for p in route_points_utm]
            bbox_utm = _bbox_with_buffer(xs, ys, span_buffer)

            run_pipeline(
                lat=lat,
                lon=lon,
                bbox_utm=bbox_utm,
                terrain_exag=params["terrain_exag"],
                colors=params["colors"],
                no_buildings=params.get("no_buildings", False),
                no_terrain=params.get("no_terrain", False),
                roof_shapes=params.get("roof_shapes", False),
                contour_interval=params.get("contour_interval"),
                border_width_mm=params.get("border_width_mm", 0.0),
                water_depth_mm=params.get("water_depth_mm", 0.8),
                building_exag=params.get("building_exag"),
                dem_source=params.get("dem_source", "glo30"),
                raceway=params.get("raceway", False),
                raceway_width=params.get("raceway_width", 1.5),
                waterways=params.get("waterways", False),
                waterway_width=params.get("waterway_width", 1.0),
                route_points_utm=route_points_utm,
                route_width=params.get("route_width", 1.5),
                scale=params.get("scale"),
                size=params.get("size", 190.0),
                color_depth_mm=params.get("color_depth_mm", 1.5),
                min_building_area=params.get("min_building_area"),
                output_dir=out_dir,
                color_grid_size=800,
                skip_stls=True,
            )

        elif to_lat is not None:
            # Two-point mode
            lat1 = params["lat"]
            lon1 = params["lon"]
            lat2 = to_lat
            lon2 = params["to_lon"]
            lat = (lat1 + lat2) / 2
            lon = (lon1 + lon2) / 2
            _, to_utm, _ = _setup_utm(lat, lon)

            x1, y1 = to_utm.transform(lon1, lat1)
            x2, y2 = to_utm.transform(lon2, lat2)
            bbox_utm = _bbox_with_buffer([x1, x2], [y1, y2], span_buffer)

            run_pipeline(
                lat=lat,
                lon=lon,
                bbox_utm=bbox_utm,
                terrain_exag=params["terrain_exag"],
                colors=params["colors"],
                no_buildings=params.get("no_buildings", False),
                no_terrain=params.get("no_terrain", False),
                roof_shapes=params.get("roof_shapes", False),
                contour_interval=params.get("contour_interval"),
                border_width_mm=params.get("border_width_mm", 0.0),
                water_depth_mm=params.get("water_depth_mm", 0.8),
                building_exag=params.get("building_exag"),
                dem_source=params.get("dem_source", "glo30"),
                raceway=params.get("raceway", False),
                raceway_width=params.get("raceway_width", 1.5),
                waterways=params.get("waterways", False),
                waterway_width=params.get("waterway_width", 1.0),
                scale=params.get("scale"),
                size=params.get("size", 190.0),
                color_depth_mm=params.get("color_depth_mm", 1.5),
                min_building_area=params.get("min_building_area"),
                output_dir=out_dir,
                color_grid_size=800,
                skip_stls=True,
            )

        else:
            # Pin mode (single lat/lon with radius)
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
                no_terrain=params.get("no_terrain", False),
                roof_shapes=params.get("roof_shapes", False),
                contour_interval=params.get("contour_interval"),
                border_width_mm=params.get("border_width_mm", 0.0),
                water_depth_mm=params.get("water_depth_mm", 0.8),
                building_exag=params.get("building_exag"),
                dem_source=params.get("dem_source", "glo30"),
                raceway=params.get("raceway", False),
                raceway_width=params.get("raceway_width", 1.5),
                waterways=params.get("waterways", False),
                waterway_width=params.get("waterway_width", 1.0),
                scale=params.get("scale"),
                size=params.get("size", 190.0),
                color_depth_mm=params.get("color_depth_mm", 1.5),
                min_building_area=params.get("min_building_area"),
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
