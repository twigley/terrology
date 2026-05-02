import pytest

from terrology.gpx import parse_gpx

GPX_WITH_NS = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <trk>
    <trkseg>
      <trkpt lat="51.5" lon="-0.12"/>
      <trkpt lat="51.6" lon="-0.11"/>
    </trkseg>
  </trk>
</gpx>
"""

GPX_NO_NS = """\
<?xml version="1.0"?>
<gpx version="1.0">
  <trk>
    <trkseg>
      <trkpt lat="10.0" lon="20.0"/>
      <trkpt lat="11.0" lon="21.0"/>
      <trkpt lat="12.0" lon="22.0"/>
    </trkseg>
  </trk>
</gpx>
"""

GPX_ROUTE = """\
<?xml version="1.0"?>
<gpx version="1.0">
  <rte>
    <rtept lat="1.0" lon="2.0"/>
    <rtept lat="3.0" lon="4.0"/>
  </rte>
</gpx>
"""

GPX_PREFERS_TRACK = """\
<?xml version="1.0"?>
<gpx version="1.0">
  <trk>
    <trkseg>
      <trkpt lat="10.0" lon="20.0"/>
    </trkseg>
  </trk>
  <rte>
    <rtept lat="99.0" lon="99.0"/>
  </rte>
</gpx>
"""

GPX_EMPTY = """\
<?xml version="1.0"?>
<gpx version="1.0"/>
"""


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_parse_gpx_with_namespace(tmp_path):
    p = _write(tmp_path, "track.gpx", GPX_WITH_NS)
    pts = parse_gpx(p)
    assert len(pts) == 2
    assert pts[0] == (51.5, -0.12)
    assert pts[1] == (51.6, -0.11)


def test_parse_gpx_no_namespace(tmp_path):
    p = _write(tmp_path, "track.gpx", GPX_NO_NS)
    pts = parse_gpx(p)
    assert len(pts) == 3
    assert pts[0] == (10.0, 20.0)


def test_parse_gpx_route_points(tmp_path):
    p = _write(tmp_path, "route.gpx", GPX_ROUTE)
    pts = parse_gpx(p)
    assert len(pts) == 2
    assert pts[0] == (1.0, 2.0)


def test_parse_gpx_prefers_track_over_route(tmp_path):
    p = _write(tmp_path, "both.gpx", GPX_PREFERS_TRACK)
    pts = parse_gpx(p)
    assert len(pts) == 1
    assert pts[0][0] == pytest.approx(10.0)  # track point, not 99.0


def test_parse_gpx_empty_raises(tmp_path):
    p = _write(tmp_path, "empty.gpx", GPX_EMPTY)
    with pytest.raises(ValueError, match="No track or route points"):
        parse_gpx(p)


def test_parse_gpx_returns_lat_lon_order(tmp_path):
    p = _write(tmp_path, "track.gpx", GPX_NO_NS)
    pts = parse_gpx(p)
    lat, lon = pts[0]
    assert lat == pytest.approx(10.0)
    assert lon == pytest.approx(20.0)
