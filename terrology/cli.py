"""
terrology — generate 3D-printable terrain maps from OpenStreetMap + elevation data.

Elevation sources (--dem):
  glo30    Copernicus GLO-30 via public S3 — no API key required (default)
  srtm     SRTM GL1 via OpenTopography — free key required
  aw3d30   ALOS AW3D30 via OpenTopography — free key required

Output files
  terrain.stl    terrain base (mono-colour printing)
  buildings.stl  building extrusions
  model.obj      combined coloured model for multi-colour slicers
  model.mtl      material colours for model.obj
  model.3mf      Bambu Studio 3MF with per-face colour metadata

Usage examples
  terrology "Snowdon" --radius 500
  terrology 51.5074,-0.1278 --radius 600 --scale 4000 --terrain-exag 3
  terrology "Zurich" --output ./zurich
  terrology "Edinburgh Castle" --to "Arthur's Seat, Edinburgh"
  terrology --area my_area.geojson
  terrology --route my_ride.gpx
"""

import argparse
import math
import sys
from pathlib import Path


def run_pipeline(
    *,
    lat: float,
    lon: float,
    radius: float = 500,
    clip_polygon_wgs84=None,
    clip_polygon_utm=None,
    bbox_utm: "tuple[float, float, float, float] | None" = None,
    scale: float | None = None,
    size: float = 190.0,
    terrain_exag: float = 2.0,
    building_exag: float | None = None,
    colors: int = 4,
    no_buildings: bool = False,
    no_terrain: bool = False,
    roof_shapes: bool = False,
    contour_interval: float | None = None,
    grid_size: int = 200,
    color_grid_size: int = 800,
    color_depth_mm: float = 1.5,
    nozzle: float = 0.4,
    output_dir: "str | Path" = "output",
    no_cache: bool = False,
    skip_stls: bool = False,
    min_building_area: float | None = None,
    water_depth_mm: float = 0.8,
    border_width_mm: float = 0.0,
    dem_source: str = "glo30",
    raceway: bool = False,
    raceway_width: float = 1.5,
    waterways: bool = False,
    waterway_width: float = 1.0,
    route_points_utm: "list[tuple[float, float]] | None" = None,
    route_width: float = 1.5,
    _mode_banner: "str | None" = None,
    _nozzle_cap_message: "str | None" = None,
) -> Path:
    """Run the terrology pipeline for a single lat/lon point with a radius.

    Extra keyword parameters (all optional, all have safe defaults):

    ``clip_polygon_utm``
        A pre-projected UTM shapely Polygon that overrides ``clip_polygon_wgs84``
        when the caller has already done the projection (e.g. after computing an
        area or shape clip in UTM space).  The WGS84 variant is still accepted for
        the common single-point / web-app call path.

    ``bbox_utm``
        ``(x_min, x_max, y_min, y_max)`` in UTM metres.  When given, skips the
        internal radius-based bbox derivation and uses these extents directly.
        Required when the caller builds the bbox from two points or a GPX track
        rather than a single centre point.

    ``no_terrain``
        Skip terrain build/export — used when only buildings + features are needed.

    ``route_points_utm``
        List of ``(x, y)`` UTM coordinate pairs representing a GPX track.  When
        provided, the route is painted onto the colour surface as a red overlay.

    ``route_width``
        Width of the painted route line on the printed model in mm (default 1.5).

    ``_mode_banner``
        Optional pre-formatted string printed instead of the default
        ``Location / Radius / Scale`` header.  Callers that already know the
        human-readable span description pass it here (area, route, two-point
        modes).

    ``_nozzle_cap_message``
        Optional pre-formatted string printed when the caller has already applied
        a nozzle cap to grid sizes and wants the cap reported in the banner.

    Returns the output directory path.
    Raises ValueError for bad parameters, RuntimeError for fetch/build failures.
    """
    from concurrent.futures import ThreadPoolExecutor

    from pyproj import CRS, Transformer

    from terrology.builder import MapBuilder, _utm_crs
    from terrology.exporter import export_3mf, export_color_stls, export_obj, export_stl
    from terrology.fetcher import (
        fetch_circuit_ways,
        fetch_elevation,
        fetch_osm_data,
        fetch_overture_buildings,
        supplement_buildings,
    )

    if colors < 1 or colors > 7:
        raise ValueError(f"colors must be 1–7, got {colors}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    utm_crs = _utm_crs(lon, lat)
    wgs84 = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    from_utm = Transformer.from_crs(utm_crs, wgs84, always_xy=True)

    cx, cy = to_utm.transform(lon, lat)

    # Resolve the clip polygon in UTM space.  Three sources in priority order:
    #   1. clip_polygon_utm — already projected (area / shape-clip paths)
    #   2. clip_polygon_wgs84 — reproject here (web app / single-point path)
    #   3. None — no clip
    if clip_polygon_utm is not None:
        area_poly_utm = clip_polygon_utm
    elif clip_polygon_wgs84 is not None:
        from shapely.ops import transform as _shp_transform

        area_poly_utm = _shp_transform(
            lambda x, y: to_utm.transform(x, y), clip_polygon_wgs84
        )
    else:
        area_poly_utm = None

    # Resolve the UTM bounding box.  When bbox_utm is given the caller has
    # already computed it (two-point / GPX / area paths); otherwise derive
    # it from the clip polygon or from the radius around the centre point.
    if bbox_utm is not None:
        x_min, x_max, y_min, y_max = bbox_utm
    elif area_poly_utm is not None:
        ab = area_poly_utm.bounds
        x_min, y_min, x_max, y_max = ab[0], ab[1], ab[2], ab[3]
    else:
        x_min, x_max = cx - radius, cx + radius
        y_min, y_max = cy - radius, cy + radius

    x_span_m = x_max - x_min
    y_span_m = y_max - y_min

    actual_scale = (
        scale if scale is not None else max(x_span_m, y_span_m) * 1000.0 / size
    )
    model_x_mm = x_span_m * 1000.0 / actual_scale
    model_y_mm = y_span_m * 1000.0 / actual_scale

    corners = [
        from_utm.transform(x, y)
        for x, y in [(x_min, y_min), (x_min, y_max), (x_max, y_min), (x_max, y_max)]
    ]
    osm_west = min(c[0] for c in corners)
    osm_east = max(c[0] for c in corners)
    osm_south = min(c[1] for c in corners)
    osm_north = max(c[1] for c in corners)

    max_useful = max(20, math.floor(max(model_x_mm, model_y_mm) / (nozzle * 2)))
    actual_grid = min(
        grid_size, max_useful
    )  # terrain base: cap at printable resolution
    actual_color_grid = min(color_grid_size, max_useful)

    # Real-world metres per colour-grid cell — drives feature skip logic in the builder.
    resolution_m = max(x_span_m, y_span_m) / actual_color_grid

    # Auto-skip buildings when a 20 m building would be shorter than 2 nozzle widths.
    # 20 m is a typical 6-storey building; below 2 nozzle widths it won't print reliably.
    _eff_bldg_exag = building_exag if building_exag is not None else terrain_exag
    _bldg_height_mm = 20.0 * _eff_bldg_exag * 1000.0 / actual_scale
    if not no_buildings and _bldg_height_mm < 2 * nozzle:
        no_buildings = True
        _auto_skip_bldg = f"{_bldg_height_mm:.2f} mm"
    else:
        _auto_skip_bldg = None

    if _nozzle_cap_message:
        print(_nozzle_cap_message)

    if _mode_banner:
        print(_mode_banner)
    else:
        print(f"\nLocation  : {lat:.5f}, {lon:.5f}")
        print(f"Radius    : {radius} m   |   Scale: 1:{actual_scale:.0f}")
    print(f"Model size: {model_x_mm:.1f} x {model_y_mm:.1f} mm")
    _skipped = _skipped_features(resolution_m)
    if _skipped:
        print(f"Resolution: {resolution_m:.1f} m/cell — skipping {_skipped} (sub-cell)")
    if _auto_skip_bldg:
        print(
            f"Buildings : skipped — 20 m building = {_auto_skip_bldg} at this scale (sub-nozzle)"
        )
    print(f"Output    : {out_dir.resolve()}\n")

    use_cache = not no_cache
    elev_pad = 0.02

    osm_skip = _skip_osm_layers(resolution_m, no_buildings, force_waterways=waterways)
    elevation = header = None
    if not no_terrain:
        print("Fetching OSM, elevation and Overture data in parallel...")
        with ThreadPoolExecutor(max_workers=3) as executor:
            osm_f = executor.submit(
                fetch_osm_data,
                south=osm_south,
                north=osm_north,
                west=osm_west,
                east=osm_east,
                use_cache=use_cache,
                skip_layers=osm_skip,
            )
            elev_f = executor.submit(
                fetch_elevation,
                south=osm_south - elev_pad,
                north=osm_north + elev_pad,
                west=osm_west - elev_pad,
                east=osm_east + elev_pad,
                use_cache=use_cache,
                dem_source=dem_source,
            )
            ov_f = (
                executor.submit(
                    fetch_overture_buildings,
                    osm_south,
                    osm_north,
                    osm_west,
                    osm_east,
                    use_cache,
                )
                if not no_buildings
                else None
            )
            osm_data = osm_f.result()
            elevation, header = elev_f.result()
        print(
            f"  Elevation: {elevation.shape[1]} x {elevation.shape[0]} cells  "
            f"(min {elevation.min():.0f} m, max {elevation.max():.0f} m)"
        )
    else:
        _need_ov = not no_buildings
        if _need_ov:
            print("Fetching OSM and Overture data in parallel...")
            with ThreadPoolExecutor(max_workers=2) as executor:
                osm_f = executor.submit(
                    fetch_osm_data,
                    south=osm_south,
                    north=osm_north,
                    west=osm_west,
                    east=osm_east,
                    use_cache=use_cache,
                    skip_layers=osm_skip,
                )
                ov_f = executor.submit(
                    fetch_overture_buildings,
                    osm_south,
                    osm_north,
                    osm_west,
                    osm_east,
                    use_cache,
                )
                osm_data = osm_f.result()
        else:
            print("Fetching OSM data...")
            osm_data = fetch_osm_data(
                south=osm_south,
                north=osm_north,
                west=osm_west,
                east=osm_east,
                use_cache=use_cache,
                skip_layers=osm_skip,
            )
            ov_f = None

    if raceway:
        print("  Fetching circuit relation member ways...")
        osm_data["circuit_ways"] = fetch_circuit_ways(
            osm_south, osm_north, osm_west, osm_east, use_cache=use_cache
        )

    min_bldg_area = (
        min_building_area
        if min_building_area is not None
        else max(4.0, actual_scale / 1000.0)
    )
    builder = MapBuilder(
        lat=lat,
        lon=lon,
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        scale=actual_scale,
        terrain_exag=terrain_exag,
        grid_size=actual_grid,
        color_depth_mm=color_depth_mm,
        color_grid_size=actual_color_grid,
        clip_poly=area_poly_utm,
        building_exag=building_exag,
        min_building_area_m2=min_bldg_area,
        water_depth_mm=water_depth_mm,
        resolution_m=resolution_m,
    )

    # --- Terrain ---
    terrain_mesh = None
    if not no_terrain:
        assert elevation is not None and header is not None
        print(f"\nBuilding terrain mesh ({actual_grid}x{actual_grid})...")
        terrain_mesh = builder.build_terrain(elevation, header, osm_data)
        if not skip_stls:
            export_stl(terrain_mesh, out_dir / "terrain.stl")

    # --- Buildings ---
    if not no_buildings:
        osm_data["buildings"] = supplement_buildings(
            osm_data.get("buildings"), ov_f.result()
        )

    buildings_mesh = None
    if not no_buildings:
        print("\nExtruding buildings...")
        buildings_mesh = builder.build_buildings(osm_data, with_roof_shapes=roof_shapes)
        if buildings_mesh is not None and not skip_stls:
            export_stl(buildings_mesh, out_dir / "buildings.stl")

    # --- Colour terrain surface ---
    terrain_face_colors = None
    if terrain_mesh is not None:
        assert builder.terrain_surface_mesh is not None
        print("\nColouring terrain faces...")
        terrain_face_colors = builder.colorize_terrain(
            builder.terrain_surface_mesh,
            osm_data,
            contour_interval_m=contour_interval,
            waterway_min_width_mm=waterway_width if waterways else None,
        )
        terrain_face_colors = _limit_colors(terrain_face_colors, colors)

        if raceway:
            print("\nHighlighting raceway...")
            terrain_face_colors = builder.colorize_raceway(
                builder.terrain_surface_mesh,
                osm_data,
                width_mm=raceway_width,
                base_colors=terrain_face_colors,
            )

        if route_points_utm is not None:
            print("\nPainting route on terrain faces...")
            terrain_face_colors = builder.colorize_route(
                builder.terrain_surface_mesh,
                route_points_utm,
                width_mm=route_width,
                base_colors=terrain_face_colors,
            )

    # --- Per-colour STLs (for slicers that can't use MTL) ---
    if terrain_face_colors is not None and not skip_stls:
        print("\nExporting per-colour STLs...")
        export_color_stls(
            builder.terrain_surface_mesh,
            terrain_face_colors,
            out_dir,
            color_depth_mm=color_depth_mm,
        )

    parts = {
        "terrain_base": builder.terrain_base_mesh,
        "terrain_top": builder.terrain_surface_mesh,
        "buildings": buildings_mesh,
    }
    parts = {k: v for k, v in parts.items() if v is not None}

    if border_width_mm > 0:
        from terrology.builder import _BASE_THICKNESS_MM
        from terrology.decorations import make_frame_mesh

        base_z = -_BASE_THICKNESS_MM
        parts["border"] = make_frame_mesh(
            model_x_mm,
            model_y_mm,
            border_width_mm,
            base_z,
            clip_poly_mm=builder.clip_poly_mm,
        )

    print("\nExporting OBJ...")
    export_obj(
        parts,
        out_dir / "model.obj",
        terrain_face_colors=terrain_face_colors,
        n_colors=colors,
    )
    print("Exporting 3MF...")
    export_3mf(
        parts,
        out_dir / "model.3mf",
        terrain_face_colors=terrain_face_colors,
        color_depth_mm=color_depth_mm,
        n_colors=colors,
    )

    print("\nDone!")
    for name, mesh in parts.items():
        e = mesh.extents  # type: ignore[union-attr]
        if e is not None:
            print(f"  {name:<12} {e[0]:.1f} x {e[1]:.1f} x {e[2]:.1f} mm")

    return out_dir


def _skip_osm_layers(
    resolution_m: float, no_buildings: bool, force_waterways: bool = False
) -> frozenset[str]:
    """OSM layer names safe to omit from the Overpass query at this resolution.

    A linear feature is only resolvable when its buffer >= resolution_m / 2.
    Area features (water, parks) are never skipped — they scale with feature size.

    Road tier buffers: major roads 10 m, secondary roads 6 m, default/minor 4 m,
    paths 1.5 m.  The roads layer is kept until min_buf > 10 so that major roads
    (motorways, trunks, primary) are fetched and painted even when minor roads and
    secondary roads are already sub-cell.  The tier-aware paint logic in
    builder.colorize_terrain handles the per-tier skip at paint time.
    """
    skip: set[str] = set()
    if no_buildings:
        skip.update({"buildings", "building_parts"})
    min_buf = resolution_m / 2
    if min_buf > 4.0:  # railways (4 m), road-type areas
        skip.update(
            {
                "railways",
                "pedestrian_areas",
                "parking",
                "aeroways",
                "piers",
                "circuits",
            }
        )
    if min_buf > 10.0:  # all road tiers are sub-cell (major roads have 10 m buffer)
        skip.add("roads")
    if min_buf > 6.0 and not force_waterways:  # wide rivers/canals (6 m buffer)
        skip.add("waterways")
    return frozenset(skip)


def _skipped_features(resolution_m: float) -> str:
    """Return a human-readable list of linear OSM feature tiers skipped at this resolution.

    A linear feature is only resolvable when its buffer >= resolution_m / 2.
    Buffers: paths 1.5 m, minor/residential roads 4 m, secondary roads 6 m,
    major roads (motorway/trunk/primary) 10 m.

    The message reflects what the tier-aware paint filter in builder.colorize_terrain
    will actually skip at paint time, which aligns with what _skip_osm_layers
    omits from the Overpass fetch.
    """
    min_buf = resolution_m / 2
    skipped = []
    if min_buf > 1.5:
        skipped.append("paths")
    if min_buf > 4.0:
        skipped.append("minor roads + railways")
    if min_buf > 6.0:
        skipped.append("secondary roads")
    if min_buf > 10.0:
        skipped.append("major roads")
    return ", ".join(skipped)


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
        "--raceway",
        action="store_true",
        help="Highlight any race circuit (OSM highway=raceway) in the mapped area as a red overlay.",
    )
    parser.add_argument(
        "--raceway-width",
        type=float,
        default=1.5,
        help="Raceway strip width on the printed model in mm (default: 1.5). "
        "Scale-independent — always this wide regardless of map area.",
    )
    parser.add_argument(
        "--waterways",
        action="store_true",
        help="Always include rivers, canals and streams, even on large maps where "
        "sub-cell waterways are normally skipped, drawn at a guaranteed minimum "
        "width. Ditches and drains stay scale-gated.",
    )
    parser.add_argument(
        "--waterway-width",
        type=float,
        default=1.0,
        help="Minimum waterway width on the printed model in mm, used with "
        "--waterways (default: 1.0). Scale-independent minimum — the real river "
        "width is used where it is larger.",
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
        "--shape",
        default="square",
        choices=["square", "circle", "hexagon"],
        help="Clip shape (default: square). circle and hexagon clip the terrain to that outline. "
        "In single-point mode the radius sets the clip size; with --to/--route the bounding box "
        "diagonal is used. --area already defines its own polygon so --shape is ignored there.",
    )
    parser.add_argument(
        "--shape-center",
        default=None,
        metavar="LAT,LON_OR_PLACE",
        help="Override the centre point for circle/hexagon shapes. "
        "Accepts 'lat,lon' or a place name. Ignored when --shape square or --area.",
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
        default=800,
        help="Color surface mesh resolution NxN (default: 800). Higher = finer roads/paths. "
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
        "--output", default="output", help="Output directory (default: ./output)"
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
        "--min-building-area",
        type=float,
        default=None,
        metavar="M2",
        help="Minimum building footprint in m² (default: auto — scale/1000, e.g. 20 m² at 1:20000). "
        "Increase to reduce noise on large-scale maps.",
    )
    parser.add_argument(
        "--water-depth",
        type=float,
        default=0.8,
        metavar="MM",
        help="Depth in mm that water bodies are recessed below terrain (default: 0.8). "
        "Converted to real-world metres based on scale and exaggeration.",
    )
    parser.add_argument(
        "--border-width",
        type=float,
        default=0.0,
        metavar="MM",
        help="Width in mm of the raised border frame around the model (default: 0 = none). "
        "Recommended: 6–8 mm.",
    )
    parser.add_argument(
        "--save-api-key",
        metavar="KEY",
        default=None,
        help="Save an OpenTopography API key to ~/.config/terrology/config and exit.",
    )
    parser.add_argument(
        "--dem",
        default="glo30",
        choices=["glo30", "srtm", "aw3d30"],
        help="Elevation source: glo30 (default, no key needed), srtm, aw3d30. "
        "srtm and aw3d30 require OPENTOPOGRAPHY_API_KEY env var "
        "(free at https://opentopography.org).",
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

    if args.save_api_key:
        from terrology.fetcher import _CONFIG_FILE, save_ot_api_key

        save_ot_api_key(args.save_api_key)
        print(f"API key saved to {_CONFIG_FILE}")
        return

    if args.colors < 1 or args.colors > 7:
        print("ERROR: --colors must be between 1 and 7.")
        sys.exit(1)

    if not args.route and not args.location and not args.area:
        print("ERROR: provide a location, --route <gpx-file>, or --area <geojson-file>")
        sys.exit(1)

    route_utm: list[tuple[float, float]] | None = None
    area_poly_utm = None  # set when --area or shape-clip is used

    # --- Resolve locations and compute UTM bounding box ---
    if args.area:
        area_poly_wgs84 = _load_area_polygon(args.area)
        if args.smooth_boundary > 0:
            area_poly_wgs84 = _chaikin_smooth(area_poly_wgs84, args.smooth_boundary)
        bnds = area_poly_wgs84.bounds  # (min_lon, min_lat, max_lon, max_lat)
        lat = (bnds[1] + bnds[3]) / 2
        lon = (bnds[0] + bnds[2]) / 2
        _, to_utm, _ = _setup_utm(lat, lon)

        from shapely.ops import transform as _shp_transform

        area_poly_utm = _shp_transform(
            lambda x, y: to_utm.transform(x, y), area_poly_wgs84
        )
        ab = area_poly_utm.bounds  # (x_min, y_min, x_max, y_max)
        x_min, x_max, y_min, y_max = _bbox_with_buffer(
            [ab[0], ab[2]], [ab[1], ab[3]], args.buffer
        )

        if args.route:
            from terrology.gpx import parse_gpx

            route_latlon = parse_gpx(Path(args.route))
            print(f"  GPX: {len(route_latlon):,} track points")
            route_utm = [to_utm.transform(plon, plat) for plat, plon in route_latlon]

    elif args.route:
        from terrology.gpx import parse_gpx

        route_latlon = parse_gpx(Path(args.route))
        print(f"  GPX: {len(route_latlon):,} track points")
        track_lats = [p[0] for p in route_latlon]
        track_lons = [p[1] for p in route_latlon]
        lat = (min(track_lats) + max(track_lats)) / 2
        lon = (min(track_lons) + max(track_lons)) / 2
        _, to_utm, _ = _setup_utm(lat, lon)

        route_utm = [to_utm.transform(plon, plat) for plat, plon in route_latlon]
        xs = [p[0] for p in route_utm]
        ys = [p[1] for p in route_utm]
        x_min, x_max, y_min, y_max = _bbox_with_buffer(xs, ys, args.buffer)
    else:
        lat1, lon1 = _resolve_location(args.location)
        if not args.to:
            # Single-point mode — delegate entirely to run_pipeline
            if args.shape != "square":
                if args.shape_center:
                    sc_lat, sc_lon = _resolve_location(args.shape_center)
                else:
                    sc_lat, sc_lon = lat1, lon1
                _clip_poly = make_shape_polygon(sc_lat, sc_lon, args.radius, args.shape)
            else:
                sc_lat, sc_lon = lat1, lon1
                _clip_poly = None
            run_pipeline(
                lat=sc_lat,
                lon=sc_lon,
                radius=args.radius,
                clip_polygon_wgs84=_clip_poly,
                scale=args.scale,
                size=args.size,
                terrain_exag=args.terrain_exag,
                building_exag=args.building_exag,
                colors=args.colors,
                no_buildings=args.no_buildings,
                no_terrain=args.no_terrain,
                roof_shapes=args.roof_shapes,
                contour_interval=args.contour_interval,
                grid_size=args.grid_size,
                color_grid_size=args.color_grid_size,
                color_depth_mm=args.color_depth,
                nozzle=args.nozzle,
                output_dir=args.output,
                no_cache=args.no_cache,
                min_building_area=args.min_building_area,
                water_depth_mm=args.water_depth,
                border_width_mm=args.border_width,
                dem_source=args.dem,
                raceway=args.raceway,
                raceway_width=args.raceway_width,
                waterways=args.waterways,
                waterway_width=args.waterway_width,
            )
            return
        lat2, lon2 = _resolve_location(args.to)
        lat = (lat1 + lat2) / 2
        lon = (lon1 + lon2) / 2
        _, to_utm, _ = _setup_utm(lat, lon)
        x1, y1 = to_utm.transform(lon1, lat1)
        x2, y2 = to_utm.transform(lon2, lat2)
        x_min, x_max, y_min, y_max = _bbox_with_buffer([x1, x2], [y1, y2], args.buffer)

    x_span_m = x_max - x_min
    y_span_m = y_max - y_min

    # Shape clipping for --to and --route: clip model to circle/hexagon.
    # --area already has its own polygon; single-point mode is handled above.
    if args.shape != "square" and area_poly_utm is None:
        from shapely.geometry import Point
        from shapely.geometry import Polygon as _Polygon

        r = max(x_span_m, y_span_m) / 2

        if args.shape_center:
            sc_lat, sc_lon = _resolve_location(args.shape_center)
            cx_utm, cy_utm = to_utm.transform(sc_lon, sc_lat)
        else:
            cx_utm = (x_min + x_max) / 2
            cy_utm = (y_min + y_max) / 2

        # Expand the data bbox to a square so the circle/hexagon never extends
        # past the fetched terrain boundary and gets clipped to a flat edge.
        x_min, x_max = cx_utm - r, cx_utm + r
        y_min, y_max = cy_utm - r, cy_utm + r
        x_span_m = y_span_m = r * 2

        if args.shape == "circle":
            area_poly_utm = Point(cx_utm, cy_utm).buffer(r, quad_segs=64)
        else:  # hexagon
            angles = [i * 2 * math.pi / 6 for i in range(6)]
            area_poly_utm = _Polygon(
                [(cx_utm + r * math.cos(a), cy_utm + r * math.sin(a)) for a in angles]
            )

    # --- Compute scale, model dimensions, and nozzle-cap grid sizes for the banner ---
    if args.scale is not None:
        scale = args.scale
    else:
        scale = max(x_span_m, y_span_m) * 1000.0 / args.size
    model_x_mm = x_span_m * 1000.0 / scale
    model_y_mm = y_span_m * 1000.0 / scale

    grid_size = args.grid_size
    color_grid_size = args.color_grid_size
    max_useful = max(20, math.floor(max(model_x_mm, model_y_mm) / (args.nozzle * 2)))
    nozzle_cap_msg: str | None = None
    if grid_size > max_useful or color_grid_size > max_useful:
        grid_size = min(grid_size, max_useful)
        color_grid_size = min(color_grid_size, max_useful)
        approx_before = (
            4 * (args.grid_size - 1) ** 2 + 8 * (args.color_grid_size - 1) ** 2
        )
        approx_after = 4 * (grid_size - 1) ** 2 + 8 * (color_grid_size - 1) ** 2
        nozzle_cap_msg = (
            f"Nozzle cap: {args.nozzle} mm  →  "
            f"grids {grid_size}×{grid_size} / {color_grid_size}×{color_grid_size}  "
            f"(~{approx_after:,} faces, was ~{approx_before:,})"
        )

    # --- Build the mode-specific banner ---
    if args.area and args.route:
        mode_banner = (
            f"\nArea      : {Path(args.area).name} + {Path(args.route).name}  |  "
            f"Span: {x_span_m:.0f} x {y_span_m:.0f} m  |  Scale: 1:{scale:.0f}"
        )
    elif args.area:
        mode_banner = (
            f"\nArea      : {Path(args.area).name}  |  "
            f"Span: {x_span_m:.0f} x {y_span_m:.0f} m  |  Scale: 1:{scale:.0f}"
        )
    elif args.route:
        mode_banner = (
            f"\nRoute     : {len(route_latlon):,} pts  |  "
            f"Span: {x_span_m:.0f} x {y_span_m:.0f} m  |  Scale: 1:{scale:.0f}"
        )
    else:
        # Two-point mode
        mode_banner = (
            f"\nFrom      : {lat1:.5f}, {lon1:.5f}\n"
            f"To        : {lat2:.5f}, {lon2:.5f}\n"
            f"Span      : {x_span_m:.0f} x {y_span_m:.0f} m   |   Scale: 1:{scale:.0f}"
        )

    # --- Delegate to run_pipeline ---
    run_pipeline(
        lat=lat,
        lon=lon,
        bbox_utm=(x_min, x_max, y_min, y_max),
        clip_polygon_utm=area_poly_utm,
        scale=scale,
        size=args.size,
        terrain_exag=args.terrain_exag,
        building_exag=args.building_exag,
        colors=args.colors,
        no_buildings=args.no_buildings,
        no_terrain=args.no_terrain,
        roof_shapes=args.roof_shapes,
        contour_interval=args.contour_interval,
        grid_size=grid_size,
        color_grid_size=color_grid_size,
        color_depth_mm=args.color_depth,
        nozzle=args.nozzle,
        output_dir=args.output,
        no_cache=args.no_cache,
        min_building_area=args.min_building_area,
        water_depth_mm=args.water_depth,
        border_width_mm=args.border_width,
        dem_source=args.dem,
        raceway=args.raceway,
        raceway_width=args.raceway_width,
        waterways=args.waterways,
        waterway_width=args.waterway_width,
        route_points_utm=route_utm,
        route_width=args.route_width,
        _mode_banner=mode_banner,
        _nozzle_cap_message=nozzle_cap_msg,
    )


def _limit_colors(face_colors, n_total: int):
    """
    Merge terrain feature colours so the total filament count stays within
    n_total.  Buildings (colour index 4) are a separate mesh object handled by
    the exporter, not a face colour, so all n_total slots are available for
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
    from shapely.ops import polygonize, unary_union

    with open(path) as f:
        data = json.load(f)
    if data.get("type") == "FeatureCollection":
        geom = shape(data["features"][0]["geometry"])
    elif data.get("type") == "Feature":
        geom = shape(data["geometry"])
    else:
        geom = shape(data)

    # Some exporters write island boundaries as LineString/MultiLineString rings.
    # Polygonize them so downstream code always gets a Polygon or MultiPolygon.
    if geom.geom_type in ("LineString", "MultiLineString"):
        polys = list(polygonize(geom))
        if not polys:
            raise ValueError(
                f"GeoJSON LineString in {path} could not be converted to a polygon "
                "(ring may not be closed)"
            )
        geom = polys[0] if len(polys) == 1 else unary_union(polys)

    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(
            f"GeoJSON geometry must be Polygon or MultiPolygon, got {geom.geom_type!r}"
        )
    return geom


def _setup_utm(lat: float, lon: float):
    from pyproj import CRS, Transformer

    from terrology.builder import _utm_crs

    utm_crs = _utm_crs(lon, lat)
    wgs84 = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    from_utm = Transformer.from_crs(utm_crs, wgs84, always_xy=True)
    return utm_crs, to_utm, from_utm


def make_shape_polygon(lat: float, lon: float, radius: float, shape: str):
    """Return a WGS84 shapely Polygon clipped to shape, centred at lat/lon with given radius."""
    import math

    from shapely.geometry import Point, Polygon
    from shapely.ops import transform as _shp_transform

    _, to_utm, from_utm = _setup_utm(lat, lon)
    cx, cy = to_utm.transform(lon, lat)

    if shape == "circle":
        poly_utm = Point(cx, cy).buffer(radius, quad_segs=64)
    elif shape == "hexagon":
        angles = [i * 2 * math.pi / 6 for i in range(6)]
        points = [
            (cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in angles
        ]
        poly_utm = Polygon(points)
    else:
        raise ValueError(f"Unknown shape: {shape!r}")

    return _shp_transform(lambda x, y: from_utm.transform(x, y), poly_utm)


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
