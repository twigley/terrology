"""Output-quality tests.

These tests verify geometric properties of the builder's output — not just
that the code runs, but that the resulting meshes and elevation arrays have
the right physical characteristics.  All tests use synthetic elevation grids
and shapely geometries so no network calls are needed.
"""

import numpy as np
import pytest
from shapely.geometry import box as shapely_box

from terrology.builder import MapBuilder, _heightfield_layer

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

UTM_EPSG = "EPSG:32630"  # zone 30N, matches lon=-0.12


def _builder(sea_poly=None, water_depth_mm=0.8, n=10, extent=1000.0):
    b = MapBuilder(
        lat=51.5,
        lon=-0.12,
        x_min=0,
        x_max=extent,
        y_min=0,
        y_max=extent,
        scale=5000,
        terrain_exag=2.0,
        grid_size=n,
        color_grid_size=n,
        water_depth_mm=water_depth_mm,
    )
    b._min_elev = 0.0
    b._sea_poly = sea_poly
    b._terrain_interp = None
    return b


def _grid(n=10, extent=1000.0):
    x1 = np.linspace(0, extent, n)
    y1 = np.linspace(0, extent, n)
    return np.meshgrid(x1, y1)


def _water_gdf(geom, natural="water"):
    import geopandas as gpd

    return gpd.GeoDataFrame({"natural": [natural]}, geometry=[geom], crs=UTM_EPSG)


# ------------------------------------------------------------------ #
# Sea depression
# ------------------------------------------------------------------ #


def test_sea_cells_depressed_land_cells_unchanged():
    """Cells inside the sea polygon are lowered; land cells stay at 0."""
    sea_poly = shapely_box(500, 0, 1000, 1000)  # right half
    b = _builder(sea_poly=sea_poly)
    gx, gy = _grid(n=20)
    elev = np.zeros_like(gx)

    b._apply_depressions(elev, {}, gx, gy, [])

    land = elev[:, gx[0] < 500]
    sea = elev[:, gx[0] >= 500]
    assert land.max() == 0.0, "land cells must not be depressed"
    assert sea.max() < 0.0, "sea cells must be depressed"


def test_sea_depression_depth_matches_water_depth_mm():
    """The actual depth in model-space mm equals the water_depth_mm setting."""
    depth_mm = 1.2
    sea_poly = shapely_box(0, 0, 1000, 1000)
    b = _builder(sea_poly=sea_poly, water_depth_mm=depth_mm)
    gx, gy = _grid()
    elev = np.zeros_like(gx)

    b._apply_depressions(elev, {}, gx, gy, [])

    # Convert the real-metre depression back to model mm
    actual_mm = abs(elev.min()) * b.terrain_exag * b.mm_per_m
    assert pytest.approx(actual_mm, abs=1e-6) == depth_mm


# ------------------------------------------------------------------ #
# Boundary rim (covered_by fix)
# ------------------------------------------------------------------ #


def test_boundary_cells_included_in_sea_depression():
    """Grid points exactly on the sea polygon boundary must be depressed.

    This covers the 'rim' bug where within() excluded edge cells.
    """
    # Sea polygon matches model extent exactly — boundary cells are ON the polygon edge
    sea_poly = shapely_box(0, 0, 1000, 1000)
    b = _builder(sea_poly=sea_poly)
    gx, gy = _grid(n=5)  # includes points at x=0, 250, 500, 750, 1000
    elev = np.zeros_like(gx)

    b._apply_depressions(elev, {}, gx, gy, [])

    assert elev.max() < 0.0, "all cells including bbox-boundary cells must be depressed"


# ------------------------------------------------------------------ #
# Bay double-depression
# ------------------------------------------------------------------ #


def test_bay_not_double_depressed():
    """A natural=bay polygon overlapping the sea polygon must not cause
    double-depression — bay cells should match the single-depression depth."""
    sea_poly = shapely_box(0, 0, 1000, 1000)
    bay_gdf = _water_gdf(shapely_box(0, 0, 1000, 1000), natural="bay")

    b = _builder(sea_poly=sea_poly)
    gx, gy = _grid()
    elev = np.zeros_like(gx)
    b._apply_depressions(elev, {"water_area": bay_gdf}, gx, gy, [])

    expected = -(b.water_depth_mm / (b.terrain_exag * b.mm_per_m))
    # All cells should sit at exactly one depression depth, not two
    assert np.allclose(elev, expected, atol=1e-10), (
        "bay cells must not be depressed twice"
    )


def test_non_bay_water_area_still_depressed():
    """A natural=water lake must still be depressed even when a sea polygon exists."""
    sea_poly = shapely_box(600, 0, 1000, 1000)  # right third is sea
    lake_gdf = _water_gdf(shapely_box(100, 100, 400, 400), natural="water")

    b = _builder(sea_poly=sea_poly)
    gx, gy = _grid(n=20)
    elev = np.zeros_like(gx)
    b._apply_depressions(elev, {"water_area": lake_gdf}, gx, gy, [])

    lake_mask = (gx > 100) & (gx < 400) & (gy > 100) & (gy < 400)
    assert elev[lake_mask].max() < 0.0, "inland lake cells must be depressed"


# ------------------------------------------------------------------ #
# Pier / bridge exclusion
# ------------------------------------------------------------------ #


def test_pier_cells_not_depressed():
    """Cells covered by a pier geometry must not be pulled down with the sea."""
    sea_poly = shapely_box(0, 0, 1000, 1000)
    pier_geom = shapely_box(400, 0, 600, 1000)  # centre strip

    b = _builder(sea_poly=sea_poly)
    gx, gy = _grid(n=20)
    elev = np.zeros_like(gx)
    b._apply_depressions(elev, {}, gx, gy, [pier_geom])

    pier_mask = (gx >= 400) & (gx <= 600)
    open_sea_mask = (gx < 400) | (gx > 600)

    assert elev[open_sea_mask].max() < 0.0, "open sea cells must be depressed"
    assert elev[pier_mask].min() == 0.0, "pier cells must stay at sea level"


# ------------------------------------------------------------------ #
# Mesh z-ordering: sea faces lower than terrain faces
# ------------------------------------------------------------------ #


def test_sea_faces_have_lower_z_than_land_faces():
    """In a colorised surface mesh, sea-coloured faces must sit below
    terrain-coloured faces, confirming the depression reaches the output."""
    extent_m = 950.0  # model extent in UTM metres
    mm_per_m = 1000.0 / 5000  # scale 1:5000
    extent_mm = extent_m * mm_per_m  # 190 mm

    n = 20
    x_mm = np.linspace(0, extent_mm, n)
    y_mm = np.linspace(0, extent_mm, n)
    xg, yg = np.meshgrid(x_mm, y_mm)

    # Simulate depressed right half (sea) in model-mm z
    water_depth_mm = 0.8
    z_top = np.zeros_like(xg)
    z_top[:, n // 2 :] = -water_depth_mm
    z_bot = np.full_like(xg, -3.0)

    mesh = _heightfield_layer(xg, yg, z_top, z_bot)

    # Sea polygon covers right half in UTM metres
    sea_poly = shapely_box(extent_m / 2, 0, extent_m, extent_m)
    b = _builder(sea_poly=sea_poly, n=n, extent=extent_m)

    color_idx = b.colorize_terrain(mesh, {})

    top_mask = mesh.face_normals[:, 2] > 0.5
    sea_faces = top_mask & (color_idx == 1)
    land_faces = top_mask & (color_idx == 0)

    assert sea_faces.any(), "mesh must contain sea-coloured faces"
    assert land_faces.any(), "mesh must contain terrain-coloured faces"

    sea_z = mesh.triangles_center[sea_faces, 2].mean()
    land_z = mesh.triangles_center[land_faces, 2].mean()
    assert sea_z < land_z, (
        f"sea faces (mean z={sea_z:.3f}) must be below land faces (mean z={land_z:.3f})"
    )
