"""
terrology — generate 3D-printable terrain maps from OpenStreetMap + OpenTopography.

Output files
  terrain.stl    terrain base (mono-colour printing)
  buildings.stl  building extrusions
  model.obj      combined coloured model for multi-colour slicers
  model.mtl      material colours for model.obj
                 Import model.obj into Bambu Studio 1.9.1+ — it reads the MTL
                 colours and lets you remap each material (terrain/water/parks/
                 buildings) to a filament.

Usage examples:
  uv run main.py "Canary Wharf, London" --radius 500 --scale 3000
  uv run main.py 51.5074,-0.1278 --radius 600 --scale 4000 --terrain-exag 3
  uv run main.py "Zurich" --api-key YOUR_KEY --output ./zurich

Get a free OpenTopography API key at: https://portal.opentopography.org/requestApiKey
Set it once with: export OPENTOPO_API_KEY=your_key
"""

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate 3D-printable terrain + building models from OSM data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "location",
        nargs="?",
        default=None,
        help='Place name or "lat,lon". Optional when --route is given.',
    )
    parser.add_argument(
        "--to",
        default=None,
        help="Second location for a two-point map. Both points sit near the edges of the model.",
    )
    parser.add_argument(
        "--route",
        default=None,
        metavar="GPX_FILE",
        help="GPX file — terrain-only map with the route painted as a coloured line",
    )
    parser.add_argument(
        "--route-width",
        type=float,
        default=1.5,
        help="Route line width on the printed model in mm (default: 1.5). "
        "Scale-independent — always this wide regardless of map area.",
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=0.05,
        help="Buffer added around the two points as a fraction of the span (default: 0.05 = 5%%). "
        "Ignored in single-point mode.",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=500,
        help="Radius in metres from the centre point (default: 500). Ignored when --to is given.",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=190.0,
        help="Longest model dimension in mm (default: 190). Scale is derived automatically.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        help="Scale denominator, overrides --size (e.g. 3000 → 1:3000)",
    )
    parser.add_argument(
        "--terrain-exag",
        type=float,
        default=2.0,
        help="Terrain vertical exaggeration (default: 2.0)",
    )
    parser.add_argument(
        "--building-exag",
        type=float,
        default=None,
        metavar="N",
        help="Building height exaggeration (default: same as --terrain-exag). "
        "Set to 1.0 for true-scale buildings, e.g. when towers already look tall enough.",
    )
    parser.add_argument(
        "--grid-size",
        type=int,
        default=200,
        help="Terrain base mesh resolution NxN (default: 200)",
    )
    parser.add_argument(
        "--color-grid-size",
        type=int,
        default=400,
        help="Color surface mesh resolution NxN (default: 400). Higher = finer roads/paths. "
        "Independent of --grid-size so the bulk mesh stays light.",
    )
    parser.add_argument(
        "--nozzle",
        type=float,
        default=0.4,
        metavar="MM",
        help="Nozzle diameter in mm (default: 0.4). Grid resolutions are capped at "
        "model_size / (2 × nozzle) — the minimum reliably printable feature is "
        "~2 nozzle widths, so finer cells are invisible and only add slicer overhead.",
    )
    parser.add_argument(
        "--dem-type",
        default="COP30",
        choices=["COP30", "SRTMGL1", "NASADEM", "AW3D30", "EU_DTM"],
        help="DEM dataset (default: COP30 = Copernicus 30 m global)",
    )
    parser.add_argument(
        "--output", default="output", help="Output directory (default: ./output)"
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENTOPO_API_KEY", ""),
        help="OpenTopography API key (or set OPENTOPO_API_KEY env var)",
    )
    parser.add_argument(
        "--color-depth",
        type=float,
        default=1.5,
        help="Depth (mm) that colour features project into the terrain (default: 1.5). "
        "Limits filament changes to just the top surface layers.",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=4,
        help="Number of filament colours (default: 4). "
        "1=terrain only, 2=+water, 3=+roads, 4=+parks, "
        "5=+buildings (separate from terrain), 6=+railways, 7=+sand/beach.",
    )
    parser.add_argument(
        "--area",
        default=None,
        metavar="GEOJSON_FILE",
        help="GeoJSON file whose first polygon defines the map boundary. "
        "location and --radius are not needed.",
    )
    parser.add_argument(
        "--no-terrain",
        action="store_true",
        help="Skip terrain (buildings + features only)",
    )
    parser.add_argument(
        "--no-buildings", action="store_true", help="Skip building extrusion"
    )
    parser.add_argument(
        "--roof-shapes",
        action="store_true",
        help="Extrude OSM roof shapes (gabled, hipped, pyramidal) above building walls. "
        "Uses roof:shape and roof:height tags where available.",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Ignore and overwrite cached downloads"
    )
    parser.add_argument(
        "--smooth-boundary",
        type=int,
        default=0,
        metavar="N",
        help="Smooth the --area polygon outline using N iterations of Chaikin corner-cutting "
        "(e.g. 3–5). Each iteration halves the sharpness of corners. Has no effect without --area.",
    )
    parser.add_argument(
        "--contour-interval",
        type=float,
        default=None,
        metavar="M",
        help="Draw elevation contour lines every M real-world metres (e.g. 50). "
        "Uses a contrasting colour from the existing 4-slot palette.",
    )
    args = parser.parse_args()

    if args.colors < 1 or args.colors > 7:
        print("ERROR: --colors must be between 1 and 7.")
        sys.exit(1)

    if not args.route and not args.location and not args.area:
        print("ERROR: provide a location, --route <gpx-file>, or --area <geojson-file>")
        sys.exit(1)
    if not args.api_key and not args.no_terrain:
        print("ERROR: OpenTopography API key required for terrain.")
        print("  Get a free key -> https://portal.opentopography.org/requestApiKey")
        print("  Then: export OPENTOPO_API_KEY=your_key  or  --api-key KEY")
        sys.exit(1)

    from terrology.builder import MapBuilder
    from terrology.exporter import export_3mf, export_color_stls, export_obj, export_stl
    from terrology.fetcher import fetch_elevation, fetch_osm_data

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    route_utm: list[tuple[float, float]] | None = None
    area_poly_utm = None  # set when --area is used

    # --- Resolve locations and compute UTM bounding box ---
    if args.area:
        area_poly_wgs84 = _load_area_polygon(args.area)
        if args.smooth_boundary > 0:
            area_poly_wgs84 = _chaikin_smooth(area_poly_wgs84, args.smooth_boundary)
        bnds = area_poly_wgs84.bounds  # (min_lon, min_lat, max_lon, max_lat)
        lat = (bnds[1] + bnds[3]) / 2
        lon = (bnds[0] + bnds[2]) / 2
        _, to_utm, from_utm = _setup_utm(lat, lon)

        from shapely.ops import transform as _shp_transform

        area_poly_utm = _shp_transform(
            lambda x, y: to_utm.transform(x, y), area_poly_wgs84
        )
        ab = area_poly_utm.bounds  # (x_min, y_min, x_max, y_max)
        x_min, x_max, y_min, y_max = _bbox_with_buffer(
            [ab[0], ab[2]], [ab[1], ab[3]], args.buffer
        )
    elif args.route:
        from terrology.gpx import parse_gpx

        route_latlon = parse_gpx(Path(args.route))
        print(f"  GPX: {len(route_latlon):,} track points")
        track_lats = [p[0] for p in route_latlon]
        track_lons = [p[1] for p in route_latlon]
        lat = (min(track_lats) + max(track_lats)) / 2
        lon = (min(track_lons) + max(track_lons)) / 2
        _, to_utm, from_utm = _setup_utm(lat, lon)

        route_utm = [to_utm.transform(plon, plat) for plat, plon in route_latlon]
        xs = [p[0] for p in route_utm]
        ys = [p[1] for p in route_utm]
        x_min, x_max, y_min, y_max = _bbox_with_buffer(xs, ys, args.buffer)
    else:
        lat1, lon1 = _resolve_location(args.location)
        if args.to:
            lat2, lon2 = _resolve_location(args.to)
            lat = (lat1 + lat2) / 2
            lon = (lon1 + lon2) / 2
        else:
            lat, lon = lat1, lon1
        _, to_utm, from_utm = _setup_utm(lat, lon)

        if args.to:
            x1, y1 = to_utm.transform(lon1, lat1)
            x2, y2 = to_utm.transform(lon2, lat2)
            x_min, x_max, y_min, y_max = _bbox_with_buffer(
                [x1, x2], [y1, y2], args.buffer
            )
        else:
            cx, cy = to_utm.transform(lon, lat)
            x_min, x_max = cx - args.radius, cx + args.radius
            y_min, y_max = cy - args.radius, cy + args.radius

    x_span_m = x_max - x_min
    y_span_m = y_max - y_min

    if args.scale is not None:
        scale = args.scale
    else:
        scale = max(x_span_m, y_span_m) * 1000.0 / args.size
    model_x_mm = x_span_m * 1000.0 / scale
    model_y_mm = y_span_m * 1000.0 / scale

    # WGS84 bbox (project all four UTM corners to get correct lon/lat extent)
    corners = [
        from_utm.transform(x, y)
        for x, y in [(x_min, y_min), (x_min, y_max), (x_max, y_min), (x_max, y_max)]
    ]
    osm_west = min(c[0] for c in corners)
    osm_east = max(c[0] for c in corners)
    osm_south = min(c[1] for c in corners)
    osm_north = max(c[1] for c in corners)

    import math

    grid_size = args.grid_size
    color_grid_size = args.color_grid_size
    max_useful = max(20, math.floor(max(model_x_mm, model_y_mm) / (args.nozzle * 2)))
    if grid_size > max_useful or color_grid_size > max_useful:
        grid_size = min(grid_size, max_useful)
        color_grid_size = min(color_grid_size, max_useful)
        approx_before = (
            4 * (args.grid_size - 1) ** 2 + 8 * (args.color_grid_size - 1) ** 2
        )
        approx_after = 4 * (grid_size - 1) ** 2 + 8 * (color_grid_size - 1) ** 2
        print(
            f"Nozzle cap: {args.nozzle} mm  →  "
            f"grids {grid_size}×{grid_size} / {color_grid_size}×{color_grid_size}  "
            f"(~{approx_after:,} faces, was ~{approx_before:,})"
        )

    if args.area:
        print(
            f"\nArea      : {Path(args.area).name}  |  "
            f"Span: {x_span_m:.0f} x {y_span_m:.0f} m  |  Scale: 1:{scale:.0f}"
        )
    elif args.route:
        print(
            f"\nRoute     : {len(route_latlon):,} pts  |  "
            f"Span: {x_span_m:.0f} x {y_span_m:.0f} m  |  Scale: 1:{scale:.0f}"
        )
    elif args.to:
        print(f"\nFrom      : {lat1:.5f}, {lon1:.5f}")
        print(f"To        : {lat2:.5f}, {lon2:.5f}")
        print(
            f"Span      : {x_span_m:.0f} x {y_span_m:.0f} m   |   Scale: 1:{scale:.0f}"
        )
    else:
        print(f"\nLocation  : {lat:.5f}, {lon:.5f}")
        print(f"Radius    : {args.radius} m   |   Scale: 1:{scale:.0f}")
    print(f"Model size: {model_x_mm:.1f} x {model_y_mm:.1f} mm")
    print(f"Output    : {out_dir.resolve()}\n")

    use_cache = not args.no_cache
    elev_pad = 0.02  # degrees — ~2 km margin around the model bbox

    builder = MapBuilder(
        lat=lat,
        lon=lon,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        scale=scale,
        terrain_exag=args.terrain_exag,
        grid_size=grid_size,
        color_depth_mm=args.color_depth,
        color_grid_size=color_grid_size,
        clip_poly=area_poly_utm,
        building_exag=args.building_exag,
    )

    # Fetch data — route mode only needs elevation; normal mode fetches both in parallel
    elevation = header = None
    if args.route:
        osm_data = {}
        if not args.no_terrain:
            print("\nFetching elevation data...")
            elevation, header = fetch_elevation(
                south=osm_south - elev_pad,
                north=osm_north + elev_pad,
                west=osm_west - elev_pad,
                east=osm_east + elev_pad,
                api_key=args.api_key,
                demtype=args.dem_type,
                use_cache=use_cache,
            )
            print(
                f"  Elevation: {elevation.shape[1]} x {elevation.shape[0]} cells  "
                f"(min {elevation.min():.0f} m, max {elevation.max():.0f} m)"
            )
    elif not args.no_terrain:
        from concurrent.futures import ThreadPoolExecutor

        print("Fetching OSM data and elevation in parallel...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            osm_f = executor.submit(
                fetch_osm_data,
                south=osm_south,
                north=osm_north,
                west=osm_west,
                east=osm_east,
                use_cache=use_cache,
            )
            elev_f = executor.submit(
                fetch_elevation,
                south=osm_south - elev_pad,
                north=osm_north + elev_pad,
                west=osm_west - elev_pad,
                east=osm_east + elev_pad,
                api_key=args.api_key,
                demtype=args.dem_type,
                use_cache=use_cache,
            )
            osm_data = osm_f.result()
            elevation, header = elev_f.result()
        print(
            f"  Elevation: {elevation.shape[1]} x {elevation.shape[0]} cells  "
            f"(min {elevation.min():.0f} m, max {elevation.max():.0f} m)"
        )
    else:
        print("Fetching OSM data...")
        osm_data = fetch_osm_data(
            south=osm_south,
            north=osm_north,
            west=osm_west,
            east=osm_east,
            use_cache=use_cache,
        )

    # --- Terrain ---
    terrain_mesh = None
    if not args.no_terrain:
        assert elevation is not None and header is not None
        print(f"\nBuilding terrain mesh ({grid_size}x{grid_size})...")
        terrain_mesh = builder.build_terrain(elevation, header, osm_data)
        export_stl(terrain_mesh, out_dir / "terrain.stl")

    # --- Buildings (skipped in route mode or when --no-buildings) ---
    buildings_mesh = None
    if not args.route and not args.no_buildings:
        print("\nExtruding buildings...")
        buildings_mesh = builder.build_buildings(
            osm_data, with_roof_shapes=args.roof_shapes
        )
        if buildings_mesh is not None:
            export_stl(buildings_mesh, out_dir / "buildings.stl")

    # --- Colour terrain surface ---
    terrain_face_colors = None
    if terrain_mesh is not None:
        assert builder.terrain_surface_mesh is not None
        if args.route:
            assert route_utm is not None
            print("\nPainting route on terrain faces...")
            terrain_face_colors = builder.colorize_route(
                builder.terrain_surface_mesh,
                route_utm,
                width_mm=args.route_width,
            )
        else:
            print("\nColouring terrain faces...")
            terrain_face_colors = builder.colorize_terrain(
                builder.terrain_surface_mesh,
                osm_data,
                contour_interval_m=args.contour_interval,
            )
            terrain_face_colors = _limit_colors(terrain_face_colors, args.colors)

    # --- Per-colour STLs (for slicers that can't use MTL) ---
    if terrain_face_colors is not None and not args.route:
        print("\nExporting per-colour STLs...")
        export_color_stls(
            builder.terrain_surface_mesh,
            terrain_face_colors,
            out_dir,
            color_depth_mm=args.color_depth,
        )

    # --- Combined coloured OBJ ---
    # terrain_base: thick base, all terrain colour — no filament changes in bulk
    # terrain_top:  thin surface layer (color_depth_mm) with water/parks/roads
    parts = {
        "terrain_base": builder.terrain_base_mesh,
        "terrain_top": builder.terrain_surface_mesh,
        "buildings": buildings_mesh,
    }
    parts = {k: v for k, v in parts.items() if v is not None}

    print("\nExporting OBJ...")
    export_obj(
        parts,
        out_dir / "model.obj",
        terrain_face_colors=terrain_face_colors,
        n_colors=args.colors,
    )

    print("Exporting 3MF...")
    export_3mf(
        parts,
        out_dir / "model.3mf",
        terrain_face_colors=terrain_face_colors,
        color_depth_mm=args.color_depth,
        n_colors=args.colors,
    )

    print("\nDone!")
    for name, mesh in parts.items():
        e = mesh.extents  # type: ignore[union-attr]
        print(f"  {name:<12} {e[0]:.1f} x {e[1]:.1f} x {e[2]:.1f} mm")


def _limit_colors(face_colors, n_total: int):
    """
    Merge terrain feature colours so the total filament count stays within
    n_total.  Buildings (slot 5) are a separate mesh object handled by the
    exporter, not a face colour, so all n_total slots are available for
    terrain-surface features.

    Merge order (least important first):
      sand(7) → terrain, railways(6) → roads, parks(2) → terrain,
      roads(3) → terrain, water(1) → terrain
    """
    slots = max(1, n_total)
    result = face_colors.copy()
    if slots < 7:
        result[result == 7] = 0  # sand → terrain
    if slots < 6:
        result[result == 6] = 3  # railways → roads
    if slots < 4:
        result[result == 2] = 0  # parks → terrain
    if slots < 3:
        result[result == 3] = 0  # roads → terrain
    if slots < 2:
        result[result == 1] = 0  # water → terrain
    return result


def _chaikin_smooth(polygon, iterations: int):
    from shapely.geometry import Polygon

    coords = list(polygon.exterior.coords[:-1])
    for _ in range(iterations):
        out = []
        n = len(coords)
        for i in range(n):
            p0 = coords[i]
            p1 = coords[(i + 1) % n]
            out.append((0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1]))
            out.append((0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1]))
        coords = out
    return Polygon(coords)


def _load_area_polygon(path: str):
    import json

    from shapely.geometry import shape

    with open(path) as f:
        data = json.load(f)
    if data.get("type") == "FeatureCollection":
        return shape(data["features"][0]["geometry"])
    if data.get("type") == "Feature":
        return shape(data["geometry"])
    return shape(data)


def _setup_utm(lat: float, lon: float):
    from pyproj import CRS, Transformer

    from terrology.builder import _utm_crs

    utm_crs = _utm_crs(lon, lat)
    wgs84 = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    from_utm = Transformer.from_crs(utm_crs, wgs84, always_xy=True)
    return utm_crs, to_utm, from_utm


def _bbox_with_buffer(
    xs,
    ys,
    buffer_frac: float,
) -> tuple[float, float, float, float]:
    x_min_, x_max_ = min(xs), max(xs)
    y_min_, y_max_ = min(ys), max(ys)
    x_buf = (x_max_ - x_min_) * buffer_frac
    y_buf = (y_max_ - y_min_) * buffer_frac
    return x_min_ - x_buf, x_max_ + x_buf, y_min_ - y_buf, y_max_ + y_buf


def _resolve_location(loc: str) -> tuple[float, float]:
    if "," in loc:
        parts = loc.split(",", 1)
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass
    import osmnx as ox

    lat, lon = ox.geocoder.geocode(loc)
    return lat, lon


if __name__ == "__main__":
    main()
