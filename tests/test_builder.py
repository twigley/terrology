import numpy as np
import pandas as pd
import pytest

from terrology.builder import (
    MapBuilder,
    _building_height,
    _building_roof_info,
    _gabled_roof,
    _heightfield_layer,
    _heightfield_solid,
    _hipped_roof,
    _pyramidal_roof,
    _utm_crs,
)

# ------------------------------------------------------------------ #
# _utm_crs
# ------------------------------------------------------------------ #


def test_utm_crs_london():
    crs = _utm_crs(-0.12, 51.5)
    assert "30" in crs.to_string()  # zone 30


def test_utm_crs_metric_distance():
    from pyproj import CRS, Transformer

    crs = _utm_crs(0.0, 1.0)
    wgs84 = CRS.from_epsg(4326)
    to_utm = Transformer.from_crs(wgs84, crs, always_xy=True)
    x0, y0 = to_utm.transform(0.0, 0.0)
    x1, y1 = to_utm.transform(0.0, 1.0)
    dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    # 1° latitude ≈ 111 km
    assert 110_000 < dist < 112_000


def test_utm_crs_antimeridian():
    # 179° E should be zone 60, -179° (181°) should be zone 1
    crs60 = _utm_crs(179.0, 0.0)
    crs1 = _utm_crs(-179.0, 0.0)
    assert "60" in crs60.to_string()
    assert "zone=1" in crs1.to_string() or " 1 " in crs1.to_string()


# ------------------------------------------------------------------ #
# _heightfield_layer / _heightfield_solid
# ------------------------------------------------------------------ #


def _flat_grid(n=4):
    x1 = np.linspace(0, 10, n)
    y1 = np.linspace(0, 10, n)
    x, y = np.meshgrid(x1, y1)
    return x, y


def test_heightfield_solid_is_watertight():
    x, y = _flat_grid()
    z = np.zeros_like(x) + 1.0
    mesh = _heightfield_solid(x, y, z)
    assert mesh.is_watertight


def test_heightfield_solid_face_count():
    n = 4
    x, y = _flat_grid(n)
    z = np.zeros_like(x)
    mesh = _heightfield_solid(x, y, z)
    # top + bottom: 2 * 2*(n-1)^2  |  walls: 4 sides * (n-1) * 2
    expected = 2 * 2 * (n - 1) ** 2 + 4 * (n - 1) * 2
    assert len(mesh.faces) == expected


def test_heightfield_layer_vertex_count():
    x, y = _flat_grid(4)
    z_top = np.ones_like(x)
    z_bot = np.zeros_like(x)
    mesh = _heightfield_layer(x, y, z_top, z_bot)
    # 4x4 grid → 16 vertices × 2 layers = 32
    assert len(mesh.vertices) == 32


def test_heightfield_layer_z_bounds():
    x, y = _flat_grid(4)
    z_top = np.ones_like(x) * 5.0
    z_bot = np.zeros_like(x)
    mesh = _heightfield_layer(x, y, z_top, z_bot)
    assert pytest.approx(mesh.bounds[0][2], abs=0.01) == 0.0
    assert pytest.approx(mesh.bounds[1][2], abs=0.01) == 5.0


# ------------------------------------------------------------------ #
# _building_height
# ------------------------------------------------------------------ #


def _row(**kwargs):
    return pd.Series(kwargs)


def test_building_height_explicit_metres():
    row = _row(height="15.0m")
    assert _building_height(row) == pytest.approx(15.0)


def test_building_height_explicit_float():
    row = _row(height=10.5)
    assert _building_height(row) == pytest.approx(10.5)


def test_building_height_levels():
    row = _row(**{"building:levels": "4"})
    assert _building_height(row) == pytest.approx(4 * 3.2)


def test_building_height_levels_fallback():
    row = _row(levels="3")
    assert _building_height(row) == pytest.approx(3 * 3.2)


def test_building_height_default():
    row = _row()
    assert _building_height(row) == pytest.approx(2 * 3.2)


def test_building_height_minimum_one():
    row = _row(height="0")
    assert _building_height(row) == pytest.approx(1.0)


def test_building_height_levels_semicolon():
    row = _row(**{"building:levels": "5;6"})
    assert _building_height(row) == pytest.approx(5 * 3.2)


# ------------------------------------------------------------------ #
# MapBuilder.build_buildings — terrain exaggeration applied to height
# ------------------------------------------------------------------ #


def test_build_buildings_applies_terrain_exag():
    """Building height in model space must scale with terrain_exag."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    footprint = Polygon([(100, 100), (200, 100), (200, 200), (100, 200)])
    gdf = gpd.GeoDataFrame(
        [{"geometry": footprint, "height": "10", "building": "yes"}],
        crs="EPSG:32630",
    )

    def _run(exag):
        b = MapBuilder(
            lat=51.5,
            lon=-0.12,
            x_min=0,
            x_max=1000,
            y_min=0,
            y_max=1000,
            scale=5000,
            terrain_exag=exag,
            grid_size=4,
        )
        mesh = b.build_buildings(
            {"buildings": gdf, "building_parts": gpd.GeoDataFrame()}
        )
        return mesh.bounds[1][2]  # top z in model mm

    top_1x = _run(1.0)
    top_2x = _run(2.0)
    assert pytest.approx(top_2x, rel=0.01) == top_1x * 2


# ------------------------------------------------------------------ #
# _building_roof_info
# ------------------------------------------------------------------ #


def test_roof_info_defaults_to_flat():
    row = _row()
    shape, h = _building_roof_info(row)
    assert shape == "flat"


def test_roof_info_reads_shape():
    row = _row(**{"roof:shape": "gabled"})
    shape, _ = _building_roof_info(row)
    assert shape == "gabled"


def test_roof_info_reads_height():
    row = _row(**{"roof:shape": "hipped", "roof:height": "4.5"})
    _, h = _building_roof_info(row)
    assert h == pytest.approx(4.5)


def test_roof_info_default_height_when_shape_set():
    from terrology.builder import _DEFAULT_ROOF_HEIGHT_M

    row = _row(**{"roof:shape": "pyramidal"})
    _, h = _building_roof_info(row)
    assert h == pytest.approx(_DEFAULT_ROOF_HEIGHT_M)


# ------------------------------------------------------------------ #
# Roof mesh generators
# ------------------------------------------------------------------ #


def _square_poly(size=10.0):
    from shapely.geometry import Polygon

    return Polygon([(0, 0), (size, 0), (size, size), (0, size)])


def test_pyramidal_roof_apex_z():
    mesh = _pyramidal_roof(_square_poly(), wall_top_z=5.0, roof_h_mm=3.0)
    assert pytest.approx(mesh.bounds[1][2], abs=0.01) == 8.0


def test_gabled_roof_ridge_z():
    mesh = _gabled_roof(_square_poly(), wall_top_z=5.0, roof_h_mm=3.0)
    assert pytest.approx(mesh.bounds[1][2], abs=0.01) == 8.0


def test_hipped_roof_ridge_z():
    mesh = _hipped_roof(_square_poly(), wall_top_z=5.0, roof_h_mm=3.0)
    assert pytest.approx(mesh.bounds[1][2], abs=0.01) == 8.0


def test_build_buildings_with_roof_shapes():
    """--roof-shapes flag produces taller mesh for non-flat roofs."""
    import geopandas as gpd
    from shapely.geometry import Polygon

    footprint = Polygon([(100, 100), (200, 100), (200, 200), (100, 200)])
    gdf = gpd.GeoDataFrame(
        [
            {
                "geometry": footprint,
                "height": "10",
                "building": "yes",
                "roof:shape": "gabled",
                "roof:height": "5",
            }
        ],
        crs="EPSG:32630",
    )

    def _run(roof_shapes):
        b = MapBuilder(
            lat=51.5,
            lon=-0.12,
            x_min=0,
            x_max=1000,
            y_min=0,
            y_max=1000,
            scale=5000,
            terrain_exag=1.0,
            grid_size=4,
            building_exag=1.0,
        )
        return b.build_buildings(
            {"buildings": gdf, "building_parts": gpd.GeoDataFrame()},
            with_roof_shapes=roof_shapes,
        ).bounds[1][2]

    assert _run(True) > _run(False)


# ------------------------------------------------------------------ #
# MapBuilder._gdf_to_utm
# ------------------------------------------------------------------ #


def test_gdf_to_utm_none_on_missing():
    builder = MapBuilder(
        lat=51.5,
        lon=-0.12,
        x_min=0,
        x_max=1000,
        y_min=0,
        y_max=1000,
        scale=5000,
        terrain_exag=2.0,
        grid_size=4,
    )
    result = builder._gdf_to_utm({}, "buildings")
    assert result is None


def test_gdf_to_utm_none_on_empty_gdf():
    import geopandas as gpd

    builder = MapBuilder(
        lat=51.5,
        lon=-0.12,
        x_min=0,
        x_max=1000,
        y_min=0,
        y_max=1000,
        scale=5000,
        terrain_exag=2.0,
        grid_size=4,
    )
    result = builder._gdf_to_utm({"roads": gpd.GeoDataFrame()}, "roads")
    assert result is None
