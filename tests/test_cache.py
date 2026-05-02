import numpy as np

from terrology import cache as _cache


def test_key_stable():
    k1 = _cache._key(1.0, 2.0, "foo")
    k2 = _cache._key(1.0, 2.0, "foo")
    assert k1 == k2


def test_key_different_args():
    assert _cache._key(1.0) != _cache._key(2.0)


def test_key_order_sensitive():
    assert _cache._key(1.0, 2.0) != _cache._key(2.0, 1.0)


def test_key_length():
    assert len(_cache._key("anything")) == 16


def test_osm_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    data = {"buildings": None, "roads": None}
    _cache.save_osm(10.0, 11.0, 20.0, 21.0, data)
    loaded = _cache.load_osm(10.0, 11.0, 20.0, 21.0)
    assert loaded == data


def test_osm_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    result = _cache.load_osm(0.0, 1.0, 0.0, 1.0)
    assert result is None


def test_elevation_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    header = {"ncols": 2.0, "nrows": 2.0, "cellsize": 0.5}
    _cache.save_elevation(10.0, 11.0, 20.0, 21.0, "COP30", arr, header)
    result = _cache.load_elevation(10.0, 11.0, 20.0, 21.0, "COP30")
    assert result is not None
    loaded_arr, loaded_hdr = result
    np.testing.assert_array_equal(loaded_arr, arr)
    assert loaded_hdr == header


def test_elevation_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    result = _cache.load_elevation(0.0, 1.0, 0.0, 1.0, "COP30")
    assert result is None


def test_elevation_demtype_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    arr = np.ones((2, 2), dtype=np.float32)
    header = {"ncols": 2.0, "nrows": 2.0, "cellsize": 1.0}
    _cache.save_elevation(0.0, 1.0, 0.0, 1.0, "COP30", arr, header)
    # Different demtype should miss
    result = _cache.load_elevation(0.0, 1.0, 0.0, 1.0, "SRTMGL1")
    assert result is None
