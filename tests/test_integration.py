"""Integration tests for run_pipeline and the area / route pipeline modes.

All tests mock fetch_osm_data and fetch_elevation to avoid network calls.
They verify that the pipeline produces the correct output files with the
expected structure for a range of configurations.
"""

import json
import sys
import zipfile
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon

from main import main, run_pipeline

# ------------------------------------------------------------------ #
# Shared test constants
# ------------------------------------------------------------------ #

_LAT, _LON, _RADIUS = 51.5, -0.12, 300.0  # London, UTM zone 30N

# Flat 10 m elevation grid covering ±0.05 lon / ±0.03 lat around the test location.
# Generous enough that no model cell maps outside the grid (fill_value path).
_ELEV_ARRAY = np.full((30, 60), 10.0, dtype=np.float32)
_ELEV_HEADER = {
    "ncols": 60.0,
    "nrows": 30.0,
    "xllcorner": -0.17,
    "yllcorner": 51.47,
    "cellsize": 0.002,
    "cellsize_y": 0.002,
    "nodata_value": -9999.0,
}

# Small grids keep every test fast (< 2 s each on typical hardware).
_FAST = dict(
    lat=_LAT,
    lon=_LON,
    radius=_RADIUS,
    grid_size=10,
    color_grid_size=20,
    no_cache=True,
)

_OSM_LAYERS = [
    "buildings",
    "building_parts",
    "roads",
    "railways",
    "water_area",
    "waterways",
    "water_landuse",
    "coastlines",
    "parks",
    "landuse_green",
    "natural_green",
    "leisure_green",
    "cemeteries",
    "aeroways",
    "parking",
    "pedestrian_areas",
    "sand",
    "piers",
]


# ------------------------------------------------------------------ #
# Helper factories
# ------------------------------------------------------------------ #


def _empty_osm() -> dict:
    return {layer: gpd.GeoDataFrame() for layer in _OSM_LAYERS}


def _wgs84_gdf(*geoms, **cols) -> gpd.GeoDataFrame:
    data = {"geometry": list(geoms)}
    data.update({k: list(v) for k, v in cols.items()})
    return gpd.GeoDataFrame(data, crs="EPSG:4326")


def _flat_elev():
    return (_ELEV_ARRAY.copy(), dict(_ELEV_HEADER))


# ------------------------------------------------------------------ #
# Pytest fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def patch_fetchers():
    """Patch both fetch functions for tests that need only empty OSM data."""
    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=_empty_osm()),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        yield


# ------------------------------------------------------------------ #
# 1. Basic 4-colour inland map
# ------------------------------------------------------------------ #


def test_basic_creates_expected_files(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, colors=4)
    assert (tmp_path / "model.obj").exists()
    assert (tmp_path / "model.mtl").exists()
    assert (tmp_path / "model.3mf").exists()
    assert (tmp_path / "terrain.stl").exists()


def test_obj_references_mtl_and_has_terrain_objects(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)
    obj = (tmp_path / "model.obj").read_text()
    assert "mtllib model.mtl" in obj
    assert "o terrain_base" in obj
    assert "o terrain_top" in obj
    assert "usemtl terrain" in obj


def test_mtl_contains_terrain_material(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)
    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl terrain" in mtl


def test_3mf_is_valid_zip(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)
    assert zipfile.is_zipfile(tmp_path / "model.3mf")


def test_3mf_contains_model_xml(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)
    with zipfile.ZipFile(tmp_path / "model.3mf") as zf:
        assert "3D/3dmodel.model" in zf.namelist()


# ------------------------------------------------------------------ #
# 2. skip_stls — only OBJ + 3MF produced
# ------------------------------------------------------------------ #


def test_skip_stls_suppresses_stl_files(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, skip_stls=True)
    assert not (tmp_path / "terrain.stl").exists()
    assert not (tmp_path / "buildings.stl").exists()
    assert (tmp_path / "model.obj").exists()
    assert (tmp_path / "model.3mf").exists()


# ------------------------------------------------------------------ #
# 3. no_buildings — buildings.stl must be absent
# ------------------------------------------------------------------ #


def test_no_buildings_flag_suppresses_stl(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, no_buildings=True)
    assert not (tmp_path / "buildings.stl").exists()


# ------------------------------------------------------------------ #
# 4. Synthetic building footprint
# ------------------------------------------------------------------ #


def test_with_buildings_creates_stl(tmp_path):
    """Synthetic 100 m × 70 m building near the map centre produces buildings.stl."""
    footprint = Polygon(
        [
            (_LON - 0.0005, _LAT - 0.0003),
            (_LON + 0.0005, _LAT - 0.0003),
            (_LON + 0.0005, _LAT + 0.0003),
            (_LON - 0.0005, _LAT + 0.0003),
        ]
    )
    osm = _empty_osm()
    osm["buildings"] = _wgs84_gdf(footprint, building=["yes"], height=["10"])

    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=osm),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=4)

    assert (tmp_path / "buildings.stl").exists()
    assert (tmp_path / "buildings.stl").stat().st_size > 0


# ------------------------------------------------------------------ #
# 5. 1-colour — MTL must contain only terrain
# ------------------------------------------------------------------ #


def test_1color_mtl_terrain_only(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, colors=1, skip_stls=True)
    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl terrain" in mtl
    for mat in ("water", "parks", "roads", "railways", "sand"):
        assert f"newmtl {mat}" not in mtl


# ------------------------------------------------------------------ #
# 6. Inland water feature
# ------------------------------------------------------------------ #


def test_inland_lake_produces_water_material(tmp_path):
    """Synthetic lake at model centre causes water material to appear in MTL."""
    lake = Polygon(
        [
            (_LON - 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT + 0.001),
            (_LON - 0.001, _LAT + 0.001),
        ]
    )
    osm = _empty_osm()
    osm["water_area"] = _wgs84_gdf(lake, natural=["water"])

    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=osm),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)

    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl water" in mtl


# ------------------------------------------------------------------ #
# 7. 7-colour map with road and water features
# ------------------------------------------------------------------ #


def test_7color_road_and_water_in_mtl(tmp_path):
    """Road and water features in OSM data cause those materials to appear in MTL."""
    lake = Polygon(
        [
            (_LON - 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT + 0.001),
            (_LON - 0.001, _LAT + 0.001),
        ]
    )
    road = LineString([(_LON - 0.002, _LAT), (_LON + 0.002, _LAT)])
    osm = _empty_osm()
    osm["water_area"] = _wgs84_gdf(lake, natural=["water"])
    osm["roads"] = _wgs84_gdf(road, highway=["primary"])

    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=osm),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=7, skip_stls=False)

    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl water" in mtl
    assert "newmtl roads" in mtl


# ------------------------------------------------------------------ #
# 8. Coastal map — sea polygon detected and coloured as water
# ------------------------------------------------------------------ #


def test_coastal_sea_produces_water_material(tmp_path):
    """East-going coastline north of centre creates a sea polygon covering the
    north strip; those faces are coloured water so water appears in the MTL."""
    # Coastline runs east at ~111 m north of centre (0.001° lat ≈ 111 m).
    # Sea is to the north (left-perpendicular of an east-going line).
    coast = LineString(
        [
            (_LON - 0.008, _LAT + 0.001),
            (_LON + 0.008, _LAT + 0.001),
        ]
    )
    osm = _empty_osm()
    osm["coastlines"] = _wgs84_gdf(coast)

    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=osm),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)

    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl water" in mtl


# ------------------------------------------------------------------ #
# 9. Border + label
# ------------------------------------------------------------------ #


def test_border_and_label_appear_in_obj(tmp_path, patch_fetchers):
    run_pipeline(
        **_FAST,
        output_dir=tmp_path,
        border_width_mm=6.0,
        label="London",
        skip_stls=True,
    )
    obj = (tmp_path / "model.obj").read_text()
    assert "o border" in obj
    assert "o scale_bar" in obj
    assert "o label" in obj


def test_border_adds_roads_material_for_scale_bar(tmp_path, patch_fetchers):
    """scale_bar and label use the roads material — must appear in MTL without extra slot."""
    run_pipeline(
        **_FAST,
        output_dir=tmp_path,
        border_width_mm=6.0,
        label="Test",
        skip_stls=True,
        colors=4,
    )
    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl roads" in mtl
    # roads is the 4th default material (index 3) — no 5th slot should appear
    assert "newmtl buildings" not in mtl


# ------------------------------------------------------------------ #
# 10. Contour interval
# ------------------------------------------------------------------ #


def test_contour_interval_runs_without_error(tmp_path, patch_fetchers):
    run_pipeline(**_FAST, output_dir=tmp_path, contour_interval=10.0, skip_stls=True)
    assert (tmp_path / "model.obj").exists()


# ------------------------------------------------------------------ #
# 11. Output directory auto-creation
# ------------------------------------------------------------------ #


def test_output_dir_created_automatically(tmp_path, patch_fetchers):
    new_dir = tmp_path / "subdir" / "nested"
    assert not new_dir.exists()
    run_pipeline(**_FAST, output_dir=new_dir, skip_stls=True)
    assert new_dir.exists()
    assert (new_dir / "model.obj").exists()


# ------------------------------------------------------------------ #
# 12. Terrain exaggeration variants
# ------------------------------------------------------------------ #


def test_exag_1_and_3_both_produce_output(tmp_path, patch_fetchers):
    for exag, name in [(1.0, "exag1"), (3.0, "exag3")]:
        d = tmp_path / name
        run_pipeline(**_FAST, output_dir=d, terrain_exag=exag, skip_stls=True)
        assert (d / "model.obj").exists()


# ------------------------------------------------------------------ #
# 13. parks / green landuse coloured as parks
# ------------------------------------------------------------------ #


def test_park_feature_produces_parks_material(tmp_path):
    """Synthetic park polygon at model centre causes parks material in MTL."""
    park = Polygon(
        [
            (_LON - 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT + 0.001),
            (_LON - 0.001, _LAT + 0.001),
        ]
    )
    osm = _empty_osm()
    osm["parks"] = _wgs84_gdf(park, leisure=["park"])

    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=osm),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=4, skip_stls=True)

    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl parks" in mtl


# ------------------------------------------------------------------ #
# 14. colors=2 — only terrain and water expected in MTL
# ------------------------------------------------------------------ #


def test_2color_no_parks_or_roads_in_mtl(tmp_path):
    """With colors=2 the only non-terrain colour is water; parks/roads collapse."""
    lake = Polygon(
        [
            (_LON - 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT - 0.001),
            (_LON + 0.001, _LAT + 0.001),
            (_LON - 0.001, _LAT + 0.001),
        ]
    )
    park = Polygon(
        [
            (_LON + 0.001, _LAT - 0.001),
            (_LON + 0.002, _LAT - 0.001),
            (_LON + 0.002, _LAT + 0.001),
            (_LON + 0.001, _LAT + 0.001),
        ]
    )
    road = LineString([(_LON - 0.002, _LAT + 0.0015), (_LON + 0.002, _LAT + 0.0015)])
    osm = _empty_osm()
    osm["water_area"] = _wgs84_gdf(lake, natural=["water"])
    osm["parks"] = _wgs84_gdf(park, leisure=["park"])
    osm["roads"] = _wgs84_gdf(road, highway=["primary"])

    with (
        patch("terrology.fetcher.fetch_osm_data", return_value=osm),
        patch("terrology.fetcher.fetch_elevation", return_value=_flat_elev()),
    ):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=2, skip_stls=True)

    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl terrain" in mtl
    assert "newmtl water" in mtl
    assert "newmtl parks" not in mtl
    assert "newmtl roads" not in mtl


# ------------------------------------------------------------------ #
# 15. ValueError for bad colors argument
# ------------------------------------------------------------------ #


def test_invalid_colors_raises_value_error(tmp_path, patch_fetchers):
    with pytest.raises(ValueError, match="colors"):
        run_pipeline(**_FAST, output_dir=tmp_path, colors=8, skip_stls=True)


# ================================================================== #
# Area (--area GeoJSON) mode tests
# ================================================================== #

# Small polygon around the London test location — ~1 km × 0.5 km.
_AREA_GEOJSON = {
    "type": "Polygon",
    "coordinates": [
        [
            [-0.130, 51.496],
            [-0.110, 51.496],
            [-0.110, 51.504],
            [-0.130, 51.504],
            [-0.130, 51.496],
        ]
    ],
}


def _write_geojson(path):
    path.write_text(json.dumps(_AREA_GEOJSON))
    return str(path)


def _run_main(argv: list[str], osm=None, elev=None):
    """Patch sys.argv and both fetchers, then call main()."""
    with (
        patch.object(sys, "argv", argv),
        patch(
            "terrology.fetcher.fetch_osm_data",
            return_value=(osm if osm is not None else _empty_osm()),
        ),
        patch(
            "terrology.fetcher.fetch_elevation",
            return_value=(elev if elev is not None else _flat_elev()),
        ),
    ):
        main()


# ------------------------------------------------------------------ #
# 16. --area basic output
# ------------------------------------------------------------------ #


def test_area_mode_creates_expected_files(tmp_path):
    geojson = _write_geojson(tmp_path / "area.geojson")
    _run_main(
        [
            "main.py",
            "--area",
            geojson,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--no-cache",
        ]
    )
    assert (tmp_path / "model.obj").exists()
    assert (tmp_path / "model.mtl").exists()
    assert (tmp_path / "model.3mf").exists()
    assert (tmp_path / "terrain.stl").exists()


def test_area_mode_obj_has_terrain_objects(tmp_path):
    geojson = _write_geojson(tmp_path / "area.geojson")
    _run_main(
        [
            "main.py",
            "--area",
            geojson,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--no-cache",
        ]
    )
    obj = (tmp_path / "model.obj").read_text()
    assert "o terrain_base" in obj
    assert "o terrain_top" in obj


def test_area_mode_with_border_and_label(tmp_path):
    geojson = _write_geojson(tmp_path / "area.geojson")
    _run_main(
        [
            "main.py",
            "--area",
            geojson,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--border-width",
            "6",
            "--label",
            "Area Test",
            "--no-cache",
        ]
    )
    obj = (tmp_path / "model.obj").read_text()
    assert "o border" in obj
    assert "o scale_bar" in obj
    assert "o label" in obj


def test_area_mode_smooth_boundary(tmp_path):
    """--smooth-boundary should run without error and produce output."""
    geojson = _write_geojson(tmp_path / "area.geojson")
    _run_main(
        [
            "main.py",
            "--area",
            geojson,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--smooth-boundary",
            "3",
            "--no-cache",
        ]
    )
    assert (tmp_path / "model.obj").exists()


def test_area_mode_7_colors(tmp_path):
    """Area mode with 7 colors and a synthetic water feature produces water in MTL."""
    lake = Polygon(
        [
            (-0.125, 51.499),
            (-0.120, 51.499),
            (-0.120, 51.501),
            (-0.125, 51.501),
        ]
    )
    osm = _empty_osm()
    osm["water_area"] = _wgs84_gdf(lake, natural=["water"])
    geojson = _write_geojson(tmp_path / "area.geojson")
    _run_main(
        [
            "main.py",
            "--area",
            geojson,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--colors",
            "7",
            "--no-cache",
        ],
        osm=osm,
    )
    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl water" in mtl


# ================================================================== #
# Route (--route GPX) mode tests
# ================================================================== #

_GPX_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="{lat0}" lon="{lon0}"/>
    <trkpt lat="{lat1}" lon="{lon1}"/>
    <trkpt lat="{lat2}" lon="{lon2}"/>
    <trkpt lat="{lat3}" lon="{lon3}"/>
    <trkpt lat="{lat4}" lon="{lon4}"/>
  </trkseg></trk>
</gpx>
"""


def _write_gpx(path):
    # Five track points forming a diagonal across the test area (~1.4 km span).
    gpx = _GPX_TEMPLATE.format(
        lat0=51.497,
        lon0=-0.128,
        lat1=51.498,
        lon1=-0.124,
        lat2=51.500,
        lon2=-0.120,
        lat3=51.502,
        lon3=-0.116,
        lat4=51.503,
        lon4=-0.112,
    )
    path.write_text(gpx)
    return str(path)


# ------------------------------------------------------------------ #
# 21. --route basic output
# ------------------------------------------------------------------ #


def test_route_mode_creates_expected_files(tmp_path):
    gpx = _write_gpx(tmp_path / "route.gpx")
    _run_main(
        [
            "main.py",
            "--route",
            gpx,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--no-cache",
        ]
    )
    assert (tmp_path / "model.obj").exists()
    assert (tmp_path / "model.mtl").exists()
    assert (tmp_path / "model.3mf").exists()
    assert (tmp_path / "terrain.stl").exists()


def test_route_mode_no_per_colour_stls(tmp_path):
    """Route mode skips per-colour STL export (no water.stl, roads.stl etc.)."""
    gpx = _write_gpx(tmp_path / "route.gpx")
    _run_main(
        [
            "main.py",
            "--route",
            gpx,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--no-cache",
        ]
    )
    for name in ("water.stl", "roads.stl", "parks.stl"):
        assert not (tmp_path / name).exists(), f"{name} should not exist in route mode"


def test_route_mode_obj_has_terrain_objects(tmp_path):
    gpx = _write_gpx(tmp_path / "route.gpx")
    _run_main(
        [
            "main.py",
            "--route",
            gpx,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "20",
            "--no-cache",
        ]
    )
    obj = (tmp_path / "model.obj").read_text()
    assert "o terrain_base" in obj
    assert "o terrain_top" in obj


def test_route_mode_paints_route_colour(tmp_path):
    """A wide route line on a fine colour grid should colour some faces as route."""
    gpx = _write_gpx(tmp_path / "route.gpx")
    _run_main(
        [
            "main.py",
            "--route",
            gpx,
            "--output",
            str(tmp_path),
            "--grid-size",
            "10",
            "--color-grid-size",
            "80",  # finer grid so route buffer spans multiple cells
            "--route-width",
            "8",  # 8 mm model width — reliably wider than grid pitch
            "--no-cache",
        ]
    )
    obj = (tmp_path / "model.obj").read_text()
    assert "usemtl route" in obj
    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl route" in mtl
