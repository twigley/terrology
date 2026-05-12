import math

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from terrology.fetcher import _parse_aaigrid, supplement_buildings

SIMPLE_GRID = """\
ncols 3
nrows 2
xllcorner 10.0
yllcorner 20.0
cellsize 0.5
nodata_value -9999
1.0 2.0 3.0
4.0 5.0 6.0
"""

NODATA_GRID = """\
ncols 3
nrows 2
xllcorner 0.0
yllcorner 0.0
cellsize 1.0
nodata_value -9999
1.0 -9999 3.0
4.0 5.0 6.0
"""

YLLCENTER_GRID = """\
ncols 2
nrows 2
xllcenter 0.5
yllcenter 0.5
cellsize 1.0
nodata_value -9999
10.0 20.0
30.0 40.0
"""


def test_parse_aaigrid_shape():
    arr, header = _parse_aaigrid(SIMPLE_GRID)
    assert arr.shape == (2, 3)


def test_parse_aaigrid_values():
    arr, _ = _parse_aaigrid(SIMPLE_GRID)
    expected = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.float32)
    np.testing.assert_array_equal(arr, expected)


def test_parse_aaigrid_header():
    _, h = _parse_aaigrid(SIMPLE_GRID)
    assert h["ncols"] == 3
    assert h["nrows"] == 2
    assert h["xllcorner"] == 10.0
    assert h["yllcorner"] == 20.0
    assert h["cellsize"] == 0.5


def test_parse_aaigrid_dtype():
    arr, _ = _parse_aaigrid(SIMPLE_GRID)
    assert arr.dtype == np.float32


def test_parse_aaigrid_nodata_filled():
    arr, _ = _parse_aaigrid(NODATA_GRID)
    assert not np.any(np.isnan(arr))
    # nodata at [0,1] should be filled with nearest valid neighbour
    assert math.isfinite(float(arr[0, 1]))


def test_parse_aaigrid_yllcenter_header():
    _, h = _parse_aaigrid(YLLCENTER_GRID)
    assert "xllcenter" in h
    assert "yllcenter" in h


# ------------------------------------------------------------------ #
# supplement_buildings
# ------------------------------------------------------------------ #


def _make_gdf(polys, extra_cols=None):
    data = {"geometry": polys}
    if extra_cols:
        data.update(extra_cols)
    return gpd.GeoDataFrame(data, crs="EPSG:4326")


def test_supplement_adds_non_overlapping():
    """Overture footprints far from any OSM building are included."""
    osm = _make_gdf([box(0, 0, 1, 1)], {"building": ["yes"]})
    overture = _make_gdf([box(5, 5, 6, 6)], {"height": [10.0], "levels": [3]})
    result = supplement_buildings(osm, overture)
    assert len(result) == 2


def test_supplement_skips_duplicate():
    """Overture footprint that largely overlaps an OSM building is skipped."""
    osm = _make_gdf([box(0, 0, 1, 1)], {"building": ["yes"]})
    # 90% overlap with the OSM building
    overture = _make_gdf(
        [box(0.05, 0.05, 0.95, 0.95)], {"height": [8.0], "levels": [2]}
    )
    result = supplement_buildings(osm, overture)
    assert len(result) == 1


def test_supplement_includes_partial_overlap():
    """Overture footprint with small overlap (<40%) is kept."""
    osm = _make_gdf([box(0, 0, 1, 1)], {"building": ["yes"]})
    # ~10% overlap
    overture = _make_gdf([box(0.9, 0.9, 1.9, 1.9)], {"height": [5.0], "levels": [1]})
    result = supplement_buildings(osm, overture)
    assert len(result) == 2


def test_supplement_empty_overture_returns_osm():
    osm = _make_gdf([box(0, 0, 1, 1)])
    overture = gpd.GeoDataFrame(
        columns=["geometry", "height", "levels"], crs="EPSG:4326"
    )
    result = supplement_buildings(osm, overture)
    assert len(result) == len(osm)


def test_supplement_empty_osm_returns_overture():
    osm = gpd.GeoDataFrame(columns=["geometry", "building"], crs="EPSG:4326")
    overture = _make_gdf([box(0, 0, 1, 1)], {"height": [5.0], "levels": [1]})
    result = supplement_buildings(osm, overture)
    assert len(result) == 1
    assert "building" in result.columns


def test_supplement_preserves_height_column():
    """Overture height values survive into the merged GDF."""
    osm = _make_gdf([box(0, 0, 1, 1)])
    overture = _make_gdf([box(5, 5, 6, 6)], {"height": [15.0], "levels": [4]})
    result = supplement_buildings(osm, overture)
    overture_row = result.iloc[-1]
    assert float(overture_row["height"]) == 15.0
