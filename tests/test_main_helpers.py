import json

import numpy as np
import pytest
from shapely.geometry import Polygon

from main import (
    _bbox_with_buffer,
    _chaikin_smooth,
    _limit_colors,
    _load_area_polygon,
    _setup_utm,
)

# ------------------------------------------------------------------ #
# _bbox_with_buffer
# ------------------------------------------------------------------ #


def test_bbox_no_buffer():
    x_min, x_max, y_min, y_max = _bbox_with_buffer([0, 10], [0, 20], 0.0)
    assert x_min == pytest.approx(0.0)
    assert x_max == pytest.approx(10.0)
    assert y_min == pytest.approx(0.0)
    assert y_max == pytest.approx(20.0)


def test_bbox_with_buffer_10pct():
    x_min, x_max, y_min, y_max = _bbox_with_buffer([0, 100], [0, 100], 0.1)
    assert x_min == pytest.approx(-10.0)
    assert x_max == pytest.approx(110.0)
    assert y_min == pytest.approx(-10.0)
    assert y_max == pytest.approx(110.0)


def test_bbox_symmetric():
    x_min, x_max, y_min, y_max = _bbox_with_buffer([10, 20], [10, 20], 0.05)
    # 5% buffer on span of 10 → 0.5 each side → span becomes 11.0
    assert x_max - x_min == pytest.approx(11.0)
    # midpoint preserved
    assert (x_min + x_max) / 2 == pytest.approx(15.0)


def test_bbox_multi_point():
    # More than 2 points; should span full range
    x_min, x_max, y_min, y_max = _bbox_with_buffer([3, 7, 1, 9], [2, 8], 0.0)
    assert x_min == pytest.approx(1.0)
    assert x_max == pytest.approx(9.0)


# ------------------------------------------------------------------ #
# _setup_utm
# ------------------------------------------------------------------ #


def test_setup_utm_returns_three():
    result = _setup_utm(51.5, -0.12)
    assert len(result) == 3


def test_setup_utm_transforms_roundtrip():
    _, to_utm, from_utm = _setup_utm(51.5, -0.12)
    lon, lat = -0.12, 51.5
    x, y = to_utm.transform(lon, lat)
    lon2, lat2 = from_utm.transform(x, y)
    assert lon2 == pytest.approx(lon, abs=1e-6)
    assert lat2 == pytest.approx(lat, abs=1e-6)


# ------------------------------------------------------------------ #
# _limit_colors
# ------------------------------------------------------------------ #


def test_limit_colors_4_unchanged():
    fc = np.array([0, 1, 2, 3])
    out = _limit_colors(fc, 4)
    np.testing.assert_array_equal(out, fc)


def test_limit_colors_3_parks_merged():
    fc = np.array([0, 1, 2, 3])
    out = _limit_colors(fc, 3)
    assert out[2] == 0  # parks → terrain
    assert out[3] == 3  # roads preserved


def test_limit_colors_2_parks_and_roads_merged():
    fc = np.array([0, 1, 2, 3])
    out = _limit_colors(fc, 2)
    assert out[2] == 0  # parks → terrain
    assert out[3] == 0  # roads → terrain
    assert out[1] == 1  # water preserved


def test_limit_colors_1_all_terrain():
    fc = np.array([0, 1, 2, 3])
    out = _limit_colors(fc, 1)
    assert all(v == 0 for v in out)


def test_limit_colors_does_not_mutate():
    fc = np.array([0, 1, 2, 3])
    orig = fc.copy()
    _limit_colors(fc, 2)
    np.testing.assert_array_equal(fc, orig)


def test_limit_colors_5_unchanged():
    fc = np.array([0, 1, 2, 3, 6, 7])
    out = _limit_colors(fc, 5)
    # buildings (4) not in face_colors; sand(7) and railways(6) both collapse
    assert out[4] == 3  # railways → roads
    assert out[5] == 0  # sand → terrain


def test_limit_colors_6_railways_kept():
    fc = np.array([0, 1, 2, 3, 6, 7])
    out = _limit_colors(fc, 6)
    assert out[4] == 6  # railways preserved
    assert out[5] == 0  # sand → terrain


def test_limit_colors_7_all_kept():
    fc = np.array([0, 1, 2, 3, 6, 7])
    out = _limit_colors(fc, 7)
    np.testing.assert_array_equal(out, fc)


def test_limit_colors_railways_cascade_to_terrain():
    # When slots<3, railways should first merge to roads then roads merge to terrain
    fc = np.array([6])
    out = _limit_colors(fc, 2)
    assert out[0] == 0  # railways → roads → terrain


def _write_geojson(tmp_path, data):
    p = tmp_path / "area.geojson"
    p.write_text(json.dumps(data))
    return str(p)


POLYGON_COORDS = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]


def test_load_area_feature_collection(tmp_path):
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": POLYGON_COORDS},
                "properties": {},
            }
        ],
    }
    poly = _load_area_polygon(_write_geojson(tmp_path, data))
    assert poly.geom_type == "Polygon"
    assert poly.is_valid


def test_load_area_feature(tmp_path):
    data = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": POLYGON_COORDS},
        "properties": {},
    }
    poly = _load_area_polygon(_write_geojson(tmp_path, data))
    assert poly.geom_type == "Polygon"


def test_load_area_geometry(tmp_path):
    data = {"type": "Polygon", "coordinates": POLYGON_COORDS}
    poly = _load_area_polygon(_write_geojson(tmp_path, data))
    assert poly.geom_type == "Polygon"


# ------------------------------------------------------------------ #
# _chaikin_smooth
# ------------------------------------------------------------------ #


def _square():
    return Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])


def test_chaikin_zero_iterations_unchanged():
    poly = _square()
    out = _chaikin_smooth(poly, 0)
    assert list(out.exterior.coords) == list(poly.exterior.coords)


def test_chaikin_returns_valid_polygon():
    out = _chaikin_smooth(_square(), 2)
    assert out.geom_type == "Polygon"
    assert out.is_valid


def test_chaikin_vertex_count_doubles_per_iteration():
    poly = _square()
    n0 = len(poly.exterior.coords) - 1  # exclude repeated closing vertex
    out1 = _chaikin_smooth(poly, 1)
    out2 = _chaikin_smooth(poly, 2)
    assert len(out1.exterior.coords) - 1 == n0 * 2
    assert len(out2.exterior.coords) - 1 == n0 * 4


def test_chaikin_output_inside_convex_hull():
    poly = _square()
    hull = poly.convex_hull
    out = _chaikin_smooth(poly, 3)
    assert hull.contains(out) or hull.equals(out) or hull.covers(out)


def test_chaikin_does_not_mutate_input():
    poly = _square()
    original_coords = list(poly.exterior.coords)
    _chaikin_smooth(poly, 2)
    assert list(poly.exterior.coords) == original_coords


# ------------------------------------------------------------------ #
# _load_area_polygon
# ------------------------------------------------------------------ #


def test_load_area_uses_first_feature(tmp_path):
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
                "properties": {},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]],
                },
                "properties": {},
            },
        ],
    }
    poly = _load_area_polygon(_write_geojson(tmp_path, data))
    # First feature's bounds, not second
    assert poly.bounds[0] < 5
