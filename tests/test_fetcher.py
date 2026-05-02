import math

import numpy as np

from terrology.fetcher import _parse_aaigrid

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
