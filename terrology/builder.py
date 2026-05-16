import numpy as np
import pandas as pd
import shapely
import trimesh
import trimesh.repair
from pyproj import CRS, Transformer
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import gaussian_filter
from shapely.affinity import scale as affine_scale
from shapely.affinity import translate
from shapely.geometry import box as shapely_box

_LEVELS_HEIGHT_M = 3.2  # metres per floor when no explicit height tag
_DEFAULT_LEVELS = 2
_DEFAULT_ROOF_HEIGHT_M = (
    3.0  # roof height when roof:shape is set but roof:height is absent
)
_ROAD_DEPRESS_M = 0.15  # terrain depression for roads (real metres)
_BASE_THICKNESS_MM = 3.0

# Road type tiers — buffer radius in real metres for each highway category
_MAJOR_ROAD_TYPES = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
    }
)
_SECONDARY_ROAD_TYPES = frozenset(
    {
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
    }
)
_PATH_TYPES = frozenset(
    {
        "footway",
        "path",
        "cycleway",
        "steps",
        "pedestrian",
        "bridleway",
        "track",
    }
)


def _utm_crs(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) / 6) + 1
    return CRS.from_dict({"proj": "utm", "zone": zone, "north": lat >= 0})


def _clip_mesh_to_polygon(mesh, clip_poly_mm):
    """
    Boolean-intersect a trimesh solid with a prism whose cross-section is
    clip_poly_mm (model mm XY coordinates). Returns the clipped mesh.
    Requires manifold3d (used automatically by trimesh 4.x).
    """
    z_lo, z_hi = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = (z_hi - z_lo) + 20.0  # 10 mm margin each side
    prism = trimesh.creation.extrude_polygon(clip_poly_mm, height=height)
    prism.apply_translation([0.0, 0.0, z_lo - 10.0])
    return trimesh.boolean.intersection(
        [mesh, prism], engine="manifold", check_volume=False
    )


class MapBuilder:
    def __init__(
        self,
        lat: float,
        lon: float,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
        scale: float,
        terrain_exag: float,
        grid_size: int,
        color_depth_mm: float = 1.5,
        color_grid_size: int = 400,
        clip_poly=None,
        building_exag: float | None = None,
        min_building_area_m2: float = 4.0,
        water_depth_mm: float = 0.8,
    ):
        self.lat = lat
        self.lon = lon
        self.scale = scale
        self.terrain_exag = terrain_exag
        self.building_exag = (
            building_exag if building_exag is not None else terrain_exag
        )
        self.grid_size = grid_size

        self.utm_crs = _utm_crs(lon, lat)
        wgs84 = CRS.from_epsg(4326)
        self._to_utm = Transformer.from_crs(wgs84, self.utm_crs, always_xy=True)

        self.x_min = x_min
        self.x_max = x_max
        self.y_min = y_min
        self.y_max = y_max

        self.mm_per_m = 1000.0 / scale
        self.color_depth_mm = color_depth_mm
        self.color_grid_size = color_grid_size
        self.min_building_area_m2 = min_building_area_m2
        self.water_depth_mm = water_depth_mm
        self.clip_poly = clip_poly  # UTM metres, or None
        if clip_poly is not None:
            from shapely.affinity import scale as _affine_scale
            from shapely.affinity import translate

            self.clip_poly_mm = _affine_scale(
                translate(clip_poly, -x_min, -y_min),
                xfact=self.mm_per_m,
                yfact=self.mm_per_m,
                origin=(0, 0),
            )
        else:
            self.clip_poly_mm = None
        self._min_elev = 0.0
        self._terrain_interp: RegularGridInterpolator | None = None
        self._sea_poly = (
            None  # built once from OSM coastline; None = fall back to elev-based
        )

        self.terrain_mesh: trimesh.Trimesh | None = None
        self.terrain_base_mesh: trimesh.Trimesh | None = None
        self.terrain_surface_mesh: trimesh.Trimesh | None = None
        self.buildings_mesh: trimesh.Trimesh | None = None

    def _gdf_to_utm(self, osm_data: dict, layer_name: str):
        gdf = osm_data.get(layer_name)
        if gdf is None or len(gdf) == 0:
            return None
        try:
            return gdf.to_crs(self.utm_crs)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Terrain
    # ------------------------------------------------------------------ #

    def build_terrain(self, elevation_arr: np.ndarray, header: dict, osm_data: dict):
        nrows, ncols = elevation_arr.shape
        xll = header.get("xllcorner", header.get("xllcenter", 0.0))
        yll = header.get("yllcorner", header.get("yllcenter", 0.0))
        cs_x = header["cellsize"]
        cs_y = header.get(
            "cellsize_y", cs_x
        )  # GLO-30 has non-square pixels at high lat

        # Geographic coordinates of each grid cell (AAIGrid: row 0 = northernmost)
        lons = xll + (np.arange(ncols) + 0.5) * cs_x
        lats = yll + (nrows - 0.5 - np.arange(nrows)) * cs_y

        # Build sea polygon early — the direction-based approach needs only the
        # coastline GDF, not the terrain interpolator.  We then zero out sea DEM cells
        # so the interpolated terrain is flat at 0 m instead of NaN-filled with
        # adjacent cliff heights (GLO-30 has no data for the open sea).
        self._sea_poly = self._build_sea_polygon(osm_data)
        if self._sea_poly is not None:
            elevation_arr = _apply_sea_mask(
                elevation_arr, header, self._sea_poly, self.utm_crs
            )

        # Regular UTM grid for the model
        gx_1d = np.linspace(self.x_min, self.x_max, self.grid_size)
        gy_1d = np.linspace(self.y_min, self.y_max, self.grid_size)
        gx, gy = np.meshgrid(gx_1d, gy_1d)

        # Interpolate using the native regular lat/lon grid — avoids Qhull/Delaunay
        # failures on near-flat grids (e.g. EU_DTM).
        lats_asc = lats[::-1]
        elev_asc = elevation_arr[::-1, :]
        fill = float(np.nanmin(elevation_arr))
        rgi = RegularGridInterpolator(
            (lats_asc, lons),
            elev_asc,
            method="linear",
            bounds_error=False,
            fill_value=fill,
        )
        wgs84 = CRS.from_epsg(4326)
        from_utm = Transformer.from_crs(self.utm_crs, wgs84, always_xy=True)
        glon, glat = from_utm.transform(gx, gy)
        elev = rgi(np.column_stack([glat.ravel(), glon.ravel()])).reshape(gx.shape)
        nans = ~np.isfinite(elev)
        if nans.any():
            rgi_nn = RegularGridInterpolator(
                (lats_asc, lons),
                elev_asc,
                method="nearest",
                bounds_error=False,
                fill_value=fill,
            )
            elev[nans] = rgi_nn(
                np.column_stack(
                    [glat.ravel()[nans.ravel()], glon.ravel()[nans.ravel()]]
                )
            )

        # Build elevated geometry early so bridge/pier cells can be shielded from
        # Gaussian smoothing.  The filter averages bridge deck pixels with adjacent
        # river pixels in the DEM, pulling the bridge elevation down toward water
        # level before the depression logic ever runs.  Saving and restoring those
        # cells preserves the raw DEM deck elevation.
        self._elevated_geoms = self._build_elevated_geoms(osm_data)
        elevated_geoms = self._elevated_geoms

        if elevated_geoms:
            import shapely as _shapely
            from shapely import STRtree as _STRtree

            _pts_c = _shapely.points(gx.ravel(), gy.ravel())
            _el_idx, _ = _STRtree(elevated_geoms).query(_pts_c, predicate="covered_by")
            _saved_elev = elev.ravel()[_el_idx].copy() if len(_el_idx) else None
        else:
            _el_idx = None
            _saved_elev = None

        elev = gaussian_filter(elev, sigma=1.0)

        if _saved_elev is not None:
            elev.ravel()[_el_idx] = _saved_elev

        self._min_elev = float(np.nanmin(elev))

        # Terrain interpolator (used by build_buildings for base heights)
        self._terrain_interp = RegularGridInterpolator(
            (gy_1d, gx_1d),
            elev,
            method="linear",
            bounds_error=False,
            fill_value=self._min_elev,
        )

        from concurrent.futures import ThreadPoolExecutor

        # Set up fine colour grid before depressions so both grids can be
        # processed in parallel.
        fine = self.color_grid_size
        gx_f1 = np.linspace(self.x_min, self.x_max, fine)
        gy_f1 = np.linspace(self.y_min, self.y_max, fine)
        gx_f, gy_f = np.meshgrid(gx_f1, gy_f1)
        elev_f = self._terrain_interp(
            np.column_stack([gy_f.ravel(), gx_f.ravel()])
        ).reshape(fine, fine)

        elev_mod = elev.copy()
        elev_f_mod = elev_f.copy()
        with ThreadPoolExecutor(max_workers=2) as ex:
            d1 = ex.submit(
                self._apply_depressions, elev_mod, osm_data, gx, gy, elevated_geoms
            )
            d2 = ex.submit(
                self._apply_depressions,
                elev_f_mod,
                osm_data,
                gx_f,
                gy_f,
                elevated_geoms,
            )
            d1.result()
            d2.result()

        # Scale both grids to model mm
        exag_mm = self.terrain_exag * self.mm_per_m
        rel_z = (elev_mod - self._min_elev) * exag_mm
        rel_z_f = (elev_f_mod - self._min_elev) * exag_mm
        model_x = (gx - self.x_min) * self.mm_per_m
        model_y = (gy - self.y_min) * self.mm_per_m
        model_x_f = (gx_f - self.x_min) * self.mm_per_m
        model_y_f = (gy_f - self.y_min) * self.mm_per_m

        # Both OBJ meshes use the fine grid so their shared z_split boundary
        # is identical — eliminates the gap that causes slicer artefacts.
        z_split_f = np.maximum(rel_z_f - self.color_depth_mm, -_BASE_THICKNESS_MM)
        z_bot_f = np.full_like(rel_z_f, -_BASE_THICKNESS_MM)

        # Build all three meshes in parallel
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_solid = ex.submit(_heightfield_solid, model_x, model_y, rel_z)
            f_base = ex.submit(
                _heightfield_layer, model_x_f, model_y_f, z_split_f, z_bot_f
            )
            f_surface = ex.submit(
                _heightfield_layer, model_x_f, model_y_f, rel_z_f, z_split_f
            )
            self.terrain_mesh = f_solid.result()
            self.terrain_base_mesh = f_base.result()
            self.terrain_surface_mesh = f_surface.result()

        if self.clip_poly_mm is not None:
            print("  Clipping meshes to polygon boundary...")
            with ThreadPoolExecutor(max_workers=3) as ex:
                f_s = ex.submit(
                    _clip_mesh_to_polygon, self.terrain_mesh, self.clip_poly_mm
                )
                f_b = ex.submit(
                    _clip_mesh_to_polygon, self.terrain_base_mesh, self.clip_poly_mm
                )
                f_t = ex.submit(
                    _clip_mesh_to_polygon, self.terrain_surface_mesh, self.clip_poly_mm
                )
                self.terrain_mesh = f_s.result()
                self.terrain_base_mesh = f_b.result()
                self.terrain_surface_mesh = f_t.result()

        return self.terrain_mesh

    def _build_sea_polygon(self, osm_data: dict):
        """
        Construct a sea polygon from OSM coastline linestrings clipped to the bbox.

        Uses the OSM coastline direction convention (sea on the left when traversing)
        to identify the sea polygon.  This avoids relying on DEM elevation values in
        sea areas, which are filled from nearest land (often > 10 m) in GLO-30.
        Falls back to centroid elevation if the direction heuristic yields no match.
        """
        from shapely.ops import polygonize_full, unary_union

        coastlines_gdf = self._gdf_to_utm(osm_data, "coastlines")
        if coastlines_gdf is None or len(coastlines_gdf) == 0:
            return None

        bbox_poly = shapely_box(self.x_min, self.y_min, self.x_max, self.y_max)
        lines = [g for g in coastlines_gdf.geometry if g is not None and not g.is_empty]
        if not lines:
            return None

        # Clip each coastline to the bbox; preserve correspondence for direction detection
        paired = [(orig, orig.intersection(bbox_poly)) for orig in lines]
        paired = [(o, c) for o, c in paired if not c.is_empty]
        if not paired:
            return None

        clip_only = [c for _, c in paired]
        combined = unary_union(clip_only + [bbox_poly.exterior])
        polys = list(polygonize_full(combined)[0].geoms)
        if not polys:
            return None

        # The user-specified location is almost always on land, so the sea polygon
        # must not contain the bbox centre.  Filter first; direction voting then
        # picks the best candidate from the survivors.
        centre = shapely_box(
            (self.x_min + self.x_max) / 2.0 - 1,
            (self.y_min + self.y_max) / 2.0 - 1,
            (self.x_min + self.x_max) / 2.0 + 1,
            (self.y_min + self.y_max) / 2.0 + 1,
        ).centroid
        sea_candidates = [p for p in polys if not p.contains(centre)]
        if not sea_candidates:
            sea_candidates = polys  # edge case: user location is at sea

        # Direction voting: use original line direction (pre-clip) so shapely's
        # intersection cannot reverse winding; clipped midpoints keep candidates
        # inside the bbox.
        orig_lines = [o for o, _ in paired]
        clip_geoms = [c for _, c in paired]

        from collections import Counter

        poly_votes: Counter = Counter()
        for sea_pt in _sea_side_candidates(orig_lines, clip_geoms):
            for i, p in enumerate(sea_candidates):
                if p.contains(sea_pt):
                    poly_votes[i] += 1

        if poly_votes:
            best_idx = poly_votes.most_common(1)[0][0]
            print("  sea polygon: identified by coastline direction")
            return sea_candidates[best_idx]

        # No directional votes — fall back to lowest centroid elevation.
        if self._terrain_interp is None:
            return sea_candidates[0] if len(sea_candidates) == 1 else None

        def _centroid_elev(p) -> float:
            c = p.centroid
            return float(self._terrain_interp([[c.y, c.x]])[0])

        sea_poly = min(sea_candidates, key=_centroid_elev)
        elev = _centroid_elev(sea_poly)
        if elev > 10.0:
            return None
        print(f"  sea polygon: centroid elevation {elev:.1f} m (fallback)")
        return sea_poly

    def _build_elevated_geoms(self, osm_data: dict) -> list:
        """Return UTM-projected geometries for piers and bridge road/rail segments.

        Called once before the parallel depression threads so _gdf_to_utm is never
        invoked from multiple threads simultaneously on the same GeoDataFrame.
        """
        # Bridge buffer must be at least as large as the coarsest grid cell so that
        # bridge centerlines always capture at least one grid point on each side.
        # Both the base grid (grid_size) and the fine colour grid (color_grid_size)
        # use these same elevated_geoms, so we take the larger cell size.
        cell_m = max(
            (self.x_max - self.x_min) / self.grid_size,
            (self.y_max - self.y_min) / self.grid_size,
            (self.x_max - self.x_min) / self.color_grid_size,
            (self.y_max - self.y_min) / self.color_grid_size,
        )
        bridge_buf = max(10.0, cell_m * 2.0)

        geoms: list = []
        pier_gdf = self._gdf_to_utm(osm_data, "piers")
        if pier_gdf is not None:
            for geom in pier_gdf.geometry:
                if geom is None or geom.is_empty:
                    continue
                if geom.geom_type in ("LineString", "MultiLineString"):
                    geom = geom.buffer(3.0)
                geoms.append(geom)
        for road_layer in ("roads", "railways"):
            road_gdf = self._gdf_to_utm(osm_data, road_layer)
            if road_gdf is not None and "bridge" in road_gdf.columns:
                bridges = road_gdf[
                    road_gdf["bridge"].notna()
                    & (road_gdf["bridge"] != "no")
                    & (road_gdf["bridge"] != False)  # noqa: E712
                ]
                for geom in bridges.geometry:
                    if geom is None or geom.is_empty:
                        continue
                    geoms.append(geom.buffer(bridge_buf))
        aero_gdf = self._gdf_to_utm(osm_data, "aeroways")
        if aero_gdf is not None and "bridge" in aero_gdf.columns:
            aero_bridges = aero_gdf[
                aero_gdf["bridge"].notna()
                & (aero_gdf["bridge"] != "no")
                & (aero_gdf["bridge"] != False)  # noqa: E712
            ]
            for geom in aero_bridges.geometry:
                if geom is None or geom.is_empty:
                    continue
                if geom.geom_type in ("LineString", "MultiLineString"):
                    geoms.append(geom.buffer(bridge_buf))
                else:
                    geoms.append(geom)
        return geoms

    def _apply_depressions(
        self,
        elev: np.ndarray,
        osm_data: dict,
        gx: np.ndarray,
        gy: np.ndarray,
        elevated_geoms: list,
    ) -> None:
        from shapely import STRtree

        # Convert model-space water depth to real metres so the recession is
        # always physically visible regardless of scale or exaggeration.
        water_depress_m = self.water_depth_mm / (self.terrain_exag * self.mm_per_m)

        pts = shapely.points(gx.ravel(), gy.ravel())

        elevated_mask = np.zeros(len(pts), dtype=bool)
        if elevated_geoms:
            idx, _ = STRtree(elevated_geoms).query(pts, predicate="covered_by")
            elevated_mask[idx] = True

        # Roads/railways are applied per-layer (different depression amount).
        for layer_name, line_buf in [("roads", 4.0), ("railways", 4.0)]:
            gdf_utm = self._gdf_to_utm(osm_data, layer_name)
            if gdf_utm is None:
                continue
            if "bridge" in gdf_utm.columns:
                keep = (
                    gdf_utm["bridge"].isna()
                    | (gdf_utm["bridge"] == "no")
                    | (gdf_utm["bridge"] == False)  # noqa: E712
                )
                gdf_utm = gdf_utm[keep]
            geoms = []
            for geom in gdf_utm.geometry:
                if geom is None or geom.is_empty:
                    continue
                if geom.geom_type in ("LineString", "MultiLineString"):
                    geom = geom.buffer(line_buf)
                geoms.append(geom)
            if not geoms:
                continue
            pt_idx, _ = STRtree(geoms).query(pts, predicate="within")
            if len(pt_idx):
                mask = np.zeros(len(pts), dtype=bool)
                mask[pt_idx] = True
                elev.ravel()[mask] -= _ROAD_DEPRESS_M

        # All water layers share the same depression depth — accumulate into one mask
        # so overlapping features (e.g. a river LineString + its area polygon) are
        # only depressed once.
        water_mask = np.zeros(len(pts), dtype=bool)

        for layer_name, line_buf in [
            ("waterways", 2.0),
            ("water_area", 0.0),
            ("water_landuse", 0.0),
        ]:
            gdf_utm = self._gdf_to_utm(osm_data, layer_name)
            if gdf_utm is None:
                continue
            if layer_name == "water_area" and "natural" in gdf_utm.columns:
                # Bays are marine features; the sea polygon handles their depression.
                gdf_utm = gdf_utm[gdf_utm["natural"] != "bay"]
            geoms = []
            for geom in gdf_utm.geometry:
                if geom is None or geom.is_empty:
                    continue
                if line_buf > 0 and geom.geom_type in ("LineString", "MultiLineString"):
                    geom = geom.buffer(line_buf)
                geoms.append(geom)
            if not geoms:
                continue
            pt_idx, _ = STRtree(geoms).query(pts, predicate="within")
            if len(pt_idx):
                water_mask[pt_idx] = True

        # Sea depression — same depth as inland water.
        # _apply_sea_mask already zeroed sea cells to 0 m, so depressing by
        # water_depress_m produces a small negative elevation that sits comfortably
        # above the 3 mm base floor.  Piers and bridges are excluded so they stay
        # at coast level (0 m) and appear raised above the recessed sea.
        if self._sea_poly is not None:
            pt_idx, _ = STRtree([self._sea_poly]).query(pts, predicate="covered_by")
            if len(pt_idx):
                water_mask[pt_idx] = True
        else:
            # Fallback when no OSM coastline polygon was found: depress by elevation threshold.
            if self._min_elev <= 1.5:
                water_mask[elev.ravel() <= 1.5] = True

        water_mask &= ~elevated_mask
        elev.ravel()[water_mask] -= water_depress_m

    # ------------------------------------------------------------------ #
    # Buildings
    # ------------------------------------------------------------------ #

    def build_buildings(self, osm_data: dict, with_roof_shapes: bool = False):
        from concurrent.futures import ThreadPoolExecutor

        bbox_poly = shapely_box(self.x_min, self.y_min, self.x_max, self.y_max)
        skipped = 0

        # --- Pass 1: collect valid (poly_utm, height_m, layer, roof_shape, roof_h_m) ---
        work_utm: list[tuple] = []
        for layer in ("buildings", "building_parts"):
            gdf_utm = self._gdf_to_utm(osm_data, layer)
            if gdf_utm is None:
                continue
            for _, row in gdf_utm.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                polys = (
                    [geom]
                    if geom.geom_type == "Polygon"
                    else list(geom.geoms)
                    if geom.geom_type == "MultiPolygon"
                    else None
                )
                if polys is None:
                    skipped += 1
                    continue
                height_m = _building_height(row)
                roof_shape, roof_h_m = (
                    _building_roof_info(row) if with_roof_shapes else ("flat", 0.0)
                )
                for poly in polys:
                    poly = poly.intersection(bbox_poly)
                    if poly.is_empty or poly.area < self.min_building_area_m2:
                        continue
                    if poly.geom_type != "Polygon":
                        skipped += 1
                        continue
                    work_utm.append((poly, height_m, layer, roof_shape, roof_h_m))

        if self.clip_poly is not None:
            work_utm = [
                t for t in work_utm if not t[0].intersection(self.clip_poly).is_empty
            ]

        if not work_utm:
            print("  No valid building meshes produced.")
            return None

        # --- Pass 2: batch terrain height lookup (one vectorised interp call) ---
        cx = np.array([t[0].centroid.x for t in work_utm])
        cy = np.array([t[0].centroid.y for t in work_utm])
        if self._terrain_interp is not None:
            terrain_z = self._terrain_interp(np.column_stack([cy, cx]))
        else:
            terrain_z = np.full(len(work_utm), self._min_elev)

        # --- Pass 3: prepare model-space args for each building ---
        mm = self.mm_per_m
        bld_exag = self.building_exag
        min_e = self._min_elev
        work_mm: list[tuple] = []
        for i, (poly, height_m, layer, roof_shape, roof_h_m) in enumerate(work_utm):
            base_z_mm = (float(terrain_z[i]) - min_e) * self.terrain_exag * mm
            wall_top_mm = base_z_mm + height_m * bld_exag * mm
            poly_mm = affine_scale(
                translate(poly, xoff=-self.x_min, yoff=-self.y_min),
                xfact=mm,
                yfact=mm,
                origin=(0, 0, 0),
            )
            work_mm.append(
                (
                    poly_mm,
                    wall_top_mm + _BASE_THICKNESS_MM,  # full_h for extrude_polygon
                    wall_top_mm,
                    roof_shape,
                    roof_h_m * bld_exag * mm,
                    layer,
                )
            )

        # --- Pass 4: parallel extrusion ---
        def _extrude(item):
            poly_mm, full_h, wall_top_mm, roof_shape, roof_h_mm, lyr = item
            try:
                wall = trimesh.creation.extrude_polygon(poly_mm, full_h)
                roof = _roof_mesh(poly_mm, wall_top_mm, roof_shape, roof_h_mm)

                if roof is not None:
                    # Remove the wall's top cap so its open top boundary aligns
                    # with the roof's open eave boundary (same XY coords, same z).
                    # merge_vertices then welds them into one closed solid.
                    top_mask = np.all(
                        np.abs(wall.vertices[wall.faces, 2] - full_h) < 1e-6, axis=1
                    )
                    wall = trimesh.Trimesh(
                        vertices=wall.vertices,
                        faces=wall.faces[~top_mask],
                        process=False,
                    )
                    wall.apply_translation([0.0, 0.0, -_BASE_THICKNESS_MM])
                    mesh = trimesh.util.concatenate([wall, roof])
                    mesh.merge_vertices()
                    trimesh.repair.fix_normals(mesh)
                else:
                    wall.apply_translation([0.0, 0.0, -_BASE_THICKNESS_MM])
                    mesh = wall

                return mesh, lyr
            except Exception:
                return None, lyr

        counts: dict[str, int] = {"buildings": 0, "building_parts": 0}
        meshes: list = []
        with ThreadPoolExecutor() as executor:
            for mesh, lyr in executor.map(_extrude, work_mm):
                if mesh is not None:
                    meshes.append(mesh)
                    counts[lyr] += 1
                else:
                    skipped += 1

        if skipped:
            print(f"  Skipped {skipped} geometries")
        if not meshes:
            print("  No valid building meshes produced.")
            return None

        # Concatenation alone leaves coincident wall edges between adjacent buildings
        # (or overlapping building_parts) which slicers flag as non-manifold after vertex
        # welding.  Union the watertight solids so shared walls are resolved; concatenate
        # any open-shell meshes (roof shapes etc.) separately.
        watertight = [m for m in meshes if m.is_volume]
        rest = [m for m in meshes if not m.is_volume]
        print(f"  Buildings: {len(watertight)} watertight, {len(rest)} open-shell")

        unified: trimesh.Trimesh | None = None
        deferred: list[trimesh.Trimesh] = []

        if watertight:
            # Attempt batch union first (fast divide-and-conquer via manifold).
            # check_volume=False skips the redundant per-mesh is_volume re-check since
            # we already filtered above.
            try:
                unified = trimesh.boolean.union(
                    watertight, engine="manifold", check_volume=False
                )
                print(f"  Building union OK: {len(unified.faces):,} faces")
            except Exception as e:
                print(
                    f"  Batch union failed ({type(e).__name__}: {e}), trying pairwise"
                )
                # Fall back to pairwise so one bad mesh doesn't abort everything
                unified = watertight[0]
                for m in watertight[1:]:
                    try:
                        unified = trimesh.boolean.union(
                            [unified, m], engine="manifold", check_volume=False
                        )
                    except Exception:
                        deferred.append(m)
                if deferred:
                    print(f"  {len(deferred)} buildings deferred (couldn't union)")

        all_parts = ([unified] if unified is not None else []) + deferred + rest
        self.buildings_mesh = (
            trimesh.util.concatenate(all_parts)
            if len(all_parts) > 1
            else (all_parts[0] if all_parts else trimesh.util.concatenate(meshes))
        )

        print(
            f"  Built {counts['buildings']} buildings, {counts['building_parts']} building parts"
        )
        return self.buildings_mesh

    # ------------------------------------------------------------------ #
    # Terrain face colouring — water and parks painted onto terrain mesh
    # ------------------------------------------------------------------ #

    def colorize_terrain(
        self,
        terrain_mesh: trimesh.Trimesh,
        osm_data: dict,
        contour_interval_m: float | None = None,
    ) -> np.ndarray:
        """Return per-face colour index: 0=terrain 1=water 2=parks 3=roads 6=railways 7=sand."""
        from shapely import STRtree

        n_faces = len(terrain_mesh.faces)
        color_idx = np.zeros(n_faces, dtype=np.int32)

        # Only query upward-facing faces — bottom and sidewall faces in the surface
        # mesh are never used by the exporter but would otherwise inflate query count ~2×.
        top_mask = terrain_mesh.face_normals[:, 2] > 0.5
        top_indices = np.where(top_mask)[0]
        centroids = terrain_mesh.triangles_center[top_mask]
        cx_utm = centroids[:, 0] / self.mm_per_m + self.x_min
        cy_utm = centroids[:, 1] / self.mm_per_m + self.y_min
        pts = shapely.points(cx_utm, cy_utm)

        def _paint_tree(geoms: list, cidx: int) -> None:
            """Paint upward-facing faces whose centroid falls inside any geometry."""
            valid = [
                g
                for g in geoms
                if g is not None
                and not g.is_empty
                and g.geom_type not in ("Point", "MultiPoint")
            ]
            if not valid:
                return
            pt_idx, _ = STRtree(valid).query(pts, predicate="within")
            if len(pt_idx):
                color_idx[top_indices[pt_idx]] = cidx

        # Paint in ascending priority (later overwrites earlier):
        # sand → sea → parks → water → roads
        # Sea after sand so the submerged part of the beach polygon (low-tide zone)
        # is correctly covered by water rather than showing as sand.

        # 0. Sand / beach — lowest priority so sea overwrites intertidal zone
        g = self._gdf_to_utm(osm_data, "sand")
        if g is not None:
            _paint_tree(list(g.geometry), 7)

        # 1. Sea — vector polygon for smooth coastline; elevation-based fallback.
        if self._sea_poly is not None:
            pt_idx, _ = STRtree([self._sea_poly]).query(pts, predicate="covered_by")
            if len(pt_idx):
                color_idx[top_indices[pt_idx]] = 1
        else:
            # Elevation-based fallback — activates when the bbox has terrain at or below
            # sea level, regardless of whether any coastline way was fetched from OSM.
            # Roads/parks painted at higher priority overwrite any misclassified town faces.
            if self._terrain_interp is not None and self._min_elev <= 1.5:
                face_elevs = self._terrain_interp(np.column_stack([cy_utm, cx_utm]))
                color_idx[top_indices[face_elevs <= 1.5]] = 1

        # 2. Parks / green landuse / natural woodland
        park_geoms: list = []
        for src in (
            "parks",
            "landuse_green",
            "natural_green",
            "leisure_green",
            "cemeteries",
        ):
            g = self._gdf_to_utm(osm_data, src)
            if g is not None:
                park_geoms.extend(g.geometry)
        _paint_tree(park_geoms, 2)

        # 2. Explicit water features
        water_geoms: list = []
        g = self._gdf_to_utm(osm_data, "water_area")
        if g is not None:
            water_geoms.extend(g.geometry)
        g = self._gdf_to_utm(osm_data, "waterways")
        if g is not None:
            _WIDE_WATERWAYS = frozenset({"river", "canal", "tidal_channel"})
            wtypes = (
                g["waterway"].tolist() if "waterway" in g.columns else [""] * len(g)
            )
            for geom, wtype in zip(g.geometry, wtypes):
                if geom is None or geom.is_empty:
                    continue
                if geom.geom_type in ("LineString", "MultiLineString"):
                    buf = 6.0 if str(wtype) in _WIDE_WATERWAYS else 2.0
                    water_geoms.append(geom.buffer(buf))
                elif geom.geom_type in ("Polygon", "MultiPolygon"):
                    water_geoms.append(geom)
        g = self._gdf_to_utm(osm_data, "water_landuse")
        if g is not None:
            water_geoms.extend(g.geometry)
        _paint_tree(water_geoms, 1)

        # 3. Roads — vectorised buffer then bulk STRtree query
        road_geoms: list = []
        g = self._gdf_to_utm(osm_data, "roads")
        if g is not None:
            geom_arr = g.geometry.values
            hw_raw = g["highway"].tolist() if "highway" in g.columns else [""] * len(g)
            hw_strs = np.array(
                [
                    (hw[0] if isinstance(hw, list) and hw else str(hw) if hw else "")
                    for hw in hw_raw
                ]
            )
            dists = np.array([_road_buffer_m(hw) for hw in hw_strs])

            line_mask = np.array(
                [
                    geom is not None
                    and not geom.is_empty
                    and geom.geom_type in ("LineString", "MultiLineString")
                    for geom in geom_arr
                ]
            )
            poly_mask = np.array(
                [
                    geom is not None
                    and not geom.is_empty
                    and geom.geom_type in ("Polygon", "MultiPolygon")
                    for geom in geom_arr
                ]
            )
            if line_mask.any():
                road_geoms.extend(shapely.buffer(geom_arr[line_mask], dists[line_mask]))
            if poly_mask.any():
                road_geoms.extend(geom_arr[poly_mask])
        # Railways — own colour slot (collapsed to roads by _limit_colors when n<6)
        g = self._gdf_to_utm(osm_data, "railways")
        if g is not None:
            rail_geoms = [
                geom.buffer(4.0)
                for geom in g.geometry
                if geom is not None
                and not geom.is_empty
                and geom.geom_type in ("LineString", "MultiLineString")
            ]
            _paint_tree(rail_geoms, 6)

        # Paved polygon areas share the roads colour slot
        for src in ("pedestrian_areas", "parking"):
            g = self._gdf_to_utm(osm_data, src)
            if g is not None:
                road_geoms.extend(
                    geom
                    for geom in g.geometry
                    if geom is not None
                    and not geom.is_empty
                    and geom.geom_type in ("Polygon", "MultiPolygon")
                )

        # Aeroways: polygon aprons/runways as-is; line taxiways/runways buffered
        g = self._gdf_to_utm(osm_data, "aeroways")
        if g is not None:
            for geom in g.geometry:
                if geom is None or geom.is_empty:
                    continue
                if geom.geom_type in ("Polygon", "MultiPolygon"):
                    road_geoms.append(geom)
                elif geom.geom_type in ("LineString", "MultiLineString"):
                    road_geoms.append(geom.buffer(15.0))

        _paint_tree(road_geoms, 3)

        # Bridges and elevated structures must never end up water-coloured: the 2D
        # polygon containment tests above can't see elevation, so a bridge over a bay
        # would otherwise get painted blue.  Re-set any water-painted faces that fall
        # inside an elevated geometry back to terrain.
        elevated = getattr(self, "_elevated_geoms", None)
        if elevated:
            el_idx, _ = STRtree(elevated).query(pts, predicate="covered_by")
            if len(el_idx):
                el_faces = top_indices[el_idx]
                color_idx[el_faces[color_idx[el_faces] == 1]] = 0

        if contour_interval_m and contour_interval_m > 0:
            exag_mm = self.terrain_exag * self.mm_per_m
            face_z = terrain_mesh.triangles[:, :, 2]  # (N, 3) z in model mm
            face_elev = face_z / exag_mm + self._min_elev  # back to real metres
            elev_min = face_elev.min(axis=1)
            elev_max = face_elev.max(axis=1)
            first_level = np.ceil(elev_min / contour_interval_m) * contour_interval_m
            is_contour = (first_level < elev_max) & (color_idx != 1)  # skip water
            base = color_idx[is_contour]
            color_idx[is_contour] = np.where(np.isin(base, [0, 2, 7]), 3, 0)
            print(
                f"  contours: {is_contour.sum():,} faces at {contour_interval_m:.0f} m intervals"
            )

        from terrology.exporter import COLOUR_NAMES

        active = sorted(int(i) for i in np.unique(color_idx))
        print(
            "  "
            + "  ".join(
                f"{COLOUR_NAMES[i]} {int((color_idx == i).sum()):,}" for i in active
            )
        )
        return color_idx

    def colorize_route(
        self,
        terrain_mesh: trimesh.Trimesh,
        route_utm: list[tuple[float, float]],
        width_mm: float = 1.5,
    ) -> np.ndarray:
        """Return per-face colour index: 0=terrain, 5=route.

        width_mm is the total strip width in model space (mm), scale-independent.
        The buffer radius is width_mm / 2.
        """
        from shapely import STRtree
        from shapely.geometry import LineString

        half_mm = width_mm / 2.0
        real_m = half_mm * self.scale / 1000.0
        print(
            f"  route width: {width_mm:.1f} mm on model  ({real_m:.0f} m real-world radius)"
        )

        # Project route from UTM → model mm
        route_mm = [
            ((x - self.x_min) * self.mm_per_m, (y - self.y_min) * self.mm_per_m)
            for x, y in route_utm
        ]

        # Buffer and query entirely in model-space mm — visually consistent
        # at any scale and aspect ratio
        centroids_mm = terrain_mesh.triangles_center[:, :2]
        pts = shapely.points(centroids_mm[:, 0], centroids_mm[:, 1])

        route_poly = LineString(route_mm).buffer(half_mm)
        pt_idx, _ = STRtree([route_poly]).query(pts, predicate="within")

        n_faces = len(terrain_mesh.faces)
        color_idx = np.zeros(n_faces, dtype=np.int32)
        color_idx[pt_idx] = 5

        pct = len(pt_idx) / n_faces * 100
        print(f"  route: {len(pt_idx):,} faces  ({pct:.1f}%)")
        return color_idx


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _building_height(row: pd.Series) -> float:
    for key in ("height", "building:height"):
        val = row.get(key)
        if val is not None and pd.notna(val):
            try:
                return max(1.0, float(str(val).replace("m", "").strip()))
            except (ValueError, TypeError):
                pass

    for key in ("building:levels", "levels"):
        val = row.get(key)
        if val is not None and pd.notna(val):
            try:
                levels = float(str(val).split(";")[0].strip())
                return max(1.0, levels * _LEVELS_HEIGHT_M)
            except (ValueError, TypeError):
                pass

    return _DEFAULT_LEVELS * _LEVELS_HEIGHT_M


def _building_roof_info(row: pd.Series) -> tuple[str, float]:
    """Return (roof_shape, roof_height_m) from OSM tags."""
    shape = "flat"
    val = row.get("roof:shape")
    if val is not None and pd.notna(val):
        shape = str(val).lower().strip()

    roof_h = _DEFAULT_ROOF_HEIGHT_M
    val = row.get("roof:height")
    if val is not None and pd.notna(val):
        try:
            roof_h = max(0.0, float(str(val).replace("m", "").strip()))
        except (ValueError, TypeError):
            pass

    return shape, roof_h


def _roof_mesh(
    poly_mm,
    wall_top_z: float,
    shape: str,
    roof_h_mm: float,
) -> trimesh.Trimesh | None:
    """Generate roof geometry on top of the wall extrusion. Returns None for flat roofs."""
    if shape == "flat" or roof_h_mm <= 0:
        return None
    try:
        if shape in ("pyramidal", "pyramid", "cone", "dome", "onion", "round"):
            return _pyramidal_roof(poly_mm, wall_top_z, roof_h_mm)
        if shape in ("gabled", "gable"):
            return _gabled_roof(poly_mm, wall_top_z, roof_h_mm)
        if shape in ("hipped", "hip", "half_hipped"):
            return _hipped_roof(poly_mm, wall_top_z, roof_h_mm)
        # mansard, gambrel, skillion, saltbox, etc. fall back to pyramidal
        return _pyramidal_roof(poly_mm, wall_top_z, roof_h_mm)
    except Exception:
        return None


def _pyramidal_roof(poly_mm, wall_top_z: float, roof_h_mm: float) -> trimesh.Trimesh:
    coords = list(poly_mm.exterior.coords[:-1])
    n = len(coords)
    c = poly_mm.centroid
    verts = np.array(
        [(x, y, wall_top_z) for x, y in coords] + [(c.x, c.y, wall_top_z + roof_h_mm)]
    )
    faces = np.array([[i, (i + 1) % n, n] for i in range(n)])
    mesh = trimesh.Trimesh(vertices=verts, faces=faces)
    trimesh.repair.fix_normals(mesh)
    return mesh


def _mrr_ridge(poly_mm, hip_fraction: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """
    Return (ridge_start_xy, ridge_end_xy) from the polygon's minimum rotated rectangle.
    hip_fraction=0 → full-length gabled ridge; hip_fraction=1 → hipped (inset by half
    the short side on each end).
    """
    mrr = poly_mm.minimum_rotated_rectangle
    pts = [np.array(c[:2]) for c in list(mrr.exterior.coords[:-1])]
    d01 = float(np.linalg.norm(pts[1] - pts[0]))
    d12 = float(np.linalg.norm(pts[2] - pts[1]))

    if d01 >= d12:
        rs = (pts[1] + pts[2]) / 2
        re = (pts[3] + pts[0]) / 2
        inset = d12 / 2 * hip_fraction
    else:
        rs = (pts[0] + pts[1]) / 2
        re = (pts[2] + pts[3]) / 2
        inset = d01 / 2 * hip_fraction

    if inset > 0:
        vec = re - rs
        length = float(np.linalg.norm(vec))
        if length > 2 * inset:
            unit = vec / length
            rs = rs + unit * inset
            re = re - unit * inset
        else:
            # Degenerate to pyramid
            c = poly_mm.centroid
            apex = np.array([c.x, c.y])
            return apex, apex

    return rs, re


def _ridge_roof(
    poly_mm, wall_top_z: float, roof_h_mm: float, hip_fraction: float
) -> trimesh.Trimesh:
    from shapely.geometry import Point as _Pt

    rs, re = _mrr_ridge(poly_mm, hip_fraction)
    if np.allclose(rs, re):
        return _pyramidal_roof(poly_mm, wall_top_z, roof_h_mm)

    ridge_z = wall_top_z + roof_h_mm
    coords = list(poly_mm.exterior.coords[:-1])
    n = len(coords)

    # Vertices: eave[0..n-1], ridge_start[n], ridge_end[n+1]
    verts = np.array(
        [(x, y, wall_top_z) for x, y in coords]
        + [(float(rs[0]), float(rs[1]), ridge_z), (float(re[0]), float(re[1]), ridge_z)]
    )
    rs_pt = _Pt(rs)
    re_pt = _Pt(re)

    faces = []
    for i in range(n):
        j = (i + 1) % n
        ri = (
            n
            if _Pt(coords[i]).distance(rs_pt) <= _Pt(coords[i]).distance(re_pt)
            else n + 1
        )
        rj = (
            n
            if _Pt(coords[j]).distance(rs_pt) <= _Pt(coords[j]).distance(re_pt)
            else n + 1
        )
        if ri == rj:
            faces.append([i, j, ri])
        else:
            faces.append([i, j, ri])
            faces.append([j, rj, ri])

    mesh = trimesh.Trimesh(vertices=verts, faces=np.array(faces))
    trimesh.repair.fix_normals(mesh)
    return mesh


def _gabled_roof(poly_mm, wall_top_z: float, roof_h_mm: float) -> trimesh.Trimesh:
    return _ridge_roof(poly_mm, wall_top_z, roof_h_mm, hip_fraction=0.0)


def _hipped_roof(poly_mm, wall_top_z: float, roof_h_mm: float) -> trimesh.Trimesh:
    return _ridge_roof(poly_mm, wall_top_z, roof_h_mm, hip_fraction=1.0)


def _apply_sea_mask(
    elevation_arr: np.ndarray,
    header: dict,
    sea_poly,
    utm_crs,
) -> np.ndarray:
    """
    Replace DEM cells that fall inside sea_poly with 0.0 m (sea level).

    GLO-30 has no coverage for the open sea; the NaN-fill in fetch_elevation()
    assigns nearby land values (often cliff heights) to those cells.  Zeroing
    them here means the interpolated terrain actually sits at sea level rather
    than appearing as a raised plateau.
    """
    import shapely

    nrows, ncols = elevation_arr.shape
    xll = header.get("xllcorner", header.get("xllcenter", 0.0))
    yll = header.get("yllcorner", header.get("yllcenter", 0.0))
    cs_x = header["cellsize"]
    cs_y = header.get("cellsize_y", cs_x)

    lons = xll + (np.arange(ncols) + 0.5) * cs_x
    lats = yll + (nrows - 0.5 - np.arange(nrows)) * cs_y
    grid_lon, grid_lat = np.meshgrid(lons, lats)

    wgs84 = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs84, utm_crs, always_xy=True)
    utm_x, utm_y = to_utm.transform(grid_lon.ravel(), grid_lat.ravel())

    from shapely import STRtree

    pts = shapely.points(utm_x, utm_y)
    idx, _ = STRtree([sea_poly]).query(pts, predicate="covered_by")
    if len(idx) == 0:
        return elevation_arr

    arr = elevation_arr.copy()
    arr.ravel()[idx] = 0.0
    return arr


def _sea_side_candidates(lines, clipped=None) -> list:
    """
    Generate candidate Points on the sea side of OSM coastline geometries.
    OSM convention: sea is on the LEFT when traversing a coastline way.

    `lines`   — original (unclipped) OSM geometries, used for direction only.
                Avoids the coordinate-reversal that shapely's intersection can
                introduce on complex linestrings.
    `clipped` — bbox-clipped counterparts; midpoints are drawn from here so
                candidates land inside the bounding box.  Defaults to `lines`.
    """
    from shapely.geometry import Point

    if clipped is None:
        clipped = lines

    candidates = []
    for orig, clip in zip(lines, clipped):
        # Direction from the ORIGINAL segment (OSM winding order preserved).
        orig_coords = (
            list(orig.coords)
            if orig.geom_type == "LineString"
            else list(orig.geoms[0].coords)
            if orig.geom_type == "MultiLineString" and orig.geoms
            else []
        )
        if len(orig_coords) < 2:
            continue
        p0 = np.array(orig_coords[0][:2])
        p1 = np.array(orig_coords[-1][:2])
        d = p1 - p0
        dist = float(np.linalg.norm(d))
        if dist < 1.0:
            continue
        d /= dist
        left = np.array([-d[1], d[0]])  # CCW rotation = left side = sea

        # Midpoints from the CLIPPED segment so candidates lie within the bbox.
        clip_segs = (
            [clip]
            if clip.geom_type == "LineString"
            else list(clip.geoms)
            if clip.geom_type == "MultiLineString"
            else []
        )
        for seg in clip_segs:
            if seg.is_empty or seg.length < 1.0:
                continue
            mid_pt = seg.interpolate(0.5, normalized=True)
            mid = np.array([mid_pt.x, mid_pt.y])
            for offset in (50.0, 200.0, 1000.0):
                candidates.append(Point(mid + left * offset))

    return candidates


def _road_buffer_m(hw: str) -> float:
    """Buffer radius in real metres for a road LineString by OSM highway type."""
    if hw in _MAJOR_ROAD_TYPES:
        return 10.0
    if hw in _SECONDARY_ROAD_TYPES:
        return 6.0
    if hw in _PATH_TYPES:
        return 1.5
    return 4.0  # residential, unclassified, service, living_street, default


def _heightfield_solid(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> trimesh.Trimesh:
    return _heightfield_layer(x, y, z_top=z, z_bot=np.full_like(z, -_BASE_THICKNESS_MM))


def _heightfield_layer(
    x: np.ndarray,
    y: np.ndarray,
    z_top: np.ndarray,
    z_bot: np.ndarray,
) -> trimesh.Trimesh:
    """
    Build a watertight solid between two height fields.
    z_top and z_bot must have the same shape (rows × cols).
    Faces are generated with vectorised numpy — much faster than Python loops
    for large grids.
    """
    rows, cols = z_top.shape
    n = rows * cols

    top_v = np.column_stack([x.ravel(), y.ravel(), z_top.ravel()])
    bot_v = np.column_stack([x.ravel(), y.ravel(), z_bot.ravel()])
    verts = np.vstack([top_v, bot_v])  # top indices 0..n-1, bottom n..2n-1

    r, c = np.meshgrid(np.arange(rows - 1), np.arange(cols - 1), indexing="ij")
    r, c = r.ravel(), c.ravel()  # type: ignore[assignment]

    t00 = r * cols + c
    t01 = r * cols + c + 1
    t10 = (r + 1) * cols + c
    t11 = (r + 1) * cols + c + 1
    b00 = t00 + n
    b01 = t01 + n
    b10 = t10 + n
    b11 = t11 + n

    # Top surface — CCW from above (normal +Z)
    top_f = np.empty((len(r) * 2, 3), dtype=np.int64)
    top_f[0::2] = np.column_stack([t00, t01, t10])
    top_f[1::2] = np.column_stack([t11, t10, t01])

    # Bottom surface — CW from above (normal -Z)
    bot_f = np.empty((len(r) * 2, 3), dtype=np.int64)
    bot_f[0::2] = np.column_stack([b00, b10, b01])
    bot_f[1::2] = np.column_stack([b11, b01, b10])

    # Walls — vectorised numpy (winding order matches original Python loops)
    ci = np.arange(cols - 1, dtype=np.int64)
    ri = np.arange(rows - 1, dtype=np.int64)

    south = np.empty((len(ci) * 2, 3), dtype=np.int64)
    south[0::2] = np.column_stack([ci, n + ci, ci + 1])
    south[1::2] = np.column_stack([n + ci, n + ci + 1, ci + 1])

    bn = np.int64((rows - 1) * cols)
    north = np.empty((len(ci) * 2, 3), dtype=np.int64)
    north[0::2] = np.column_stack([bn + ci, bn + ci + 1, n + bn + ci])
    north[1::2] = np.column_stack([n + bn + ci, bn + ci + 1, n + bn + ci + 1])

    west = np.empty((len(ri) * 2, 3), dtype=np.int64)
    west[0::2] = np.column_stack([ri * cols, (ri + 1) * cols, n + ri * cols])
    west[1::2] = np.column_stack([n + ri * cols, (ri + 1) * cols, n + (ri + 1) * cols])

    ec = np.int64(cols - 1)
    east = np.empty((len(ri) * 2, 3), dtype=np.int64)
    east[0::2] = np.column_stack(
        [ri * cols + ec, n + ri * cols + ec, (ri + 1) * cols + ec]
    )
    east[1::2] = np.column_stack(
        [n + ri * cols + ec, n + (ri + 1) * cols + ec, (ri + 1) * cols + ec]
    )

    all_faces = np.vstack([top_f, bot_f, south, north, west, east])
    mesh = trimesh.Trimesh(vertices=verts, faces=all_faces)
    trimesh.repair.fix_normals(mesh)
    return mesh
