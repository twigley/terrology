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


def test_osm_corrupt_pickle_returns_none(tmp_path, monkeypatch):
    """A corrupt OSM pickle file should be treated as a cache miss, not raise an error."""
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    # Create a corrupt pickle file
    pkl_path = _cache._osm_path(10.0, 11.0, 20.0, 21.0)
    pkl_path.mkdir(parents=True, exist_ok=True)
    with open(pkl_path / "features.pkl", "wb") as f:
        f.write(b"this is not valid pickle data")
    # Should return None instead of raising
    result = _cache.load_osm(10.0, 11.0, 20.0, 21.0)
    assert result is None


def test_elevation_corrupt_npz_returns_none(tmp_path, monkeypatch):
    """A corrupt elevation NPZ file should be treated as a cache miss, not raise an error."""
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    # Create a corrupt npz file
    elev_path = _cache._elev_path(0.0, 1.0, 0.0, 1.0, "COP30")
    elev_path.mkdir(parents=True, exist_ok=True)
    with open(elev_path / "data.npz", "wb") as f:
        f.write(b"not a valid npz file")
    # Create a valid header so it tries to load
    import json

    with open(elev_path / "header.json", "w") as f:
        json.dump({"ncols": 2.0, "nrows": 2.0}, f)
    # Should return None instead of raising
    result = _cache.load_elevation(0.0, 1.0, 0.0, 1.0, "COP30")
    assert result is None


def test_circuit_ways_corrupt_pickle_returns_none(tmp_path, monkeypatch):
    """A corrupt circuit ways pickle file should be treated as a cache miss."""
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    # Create a corrupt pickle file
    pkl_path = _cache._circuit_ways_path(10.0, 11.0, 20.0, 21.0)
    pkl_path.mkdir(parents=True, exist_ok=True)
    with open(pkl_path / _cache._CIRCUIT_WAYS_PKL, "wb") as f:
        f.write(b"corrupt pickle")
    result = _cache.load_circuit_ways(10.0, 11.0, 20.0, 21.0)
    assert result is None


def test_overture_corrupt_pickle_returns_none(tmp_path, monkeypatch):
    """A corrupt Overture buildings pickle file should be treated as a cache miss."""
    monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
    # Create a corrupt pickle file
    pkl_path = _cache._overture_path(10.0, 11.0, 20.0, 21.0)
    pkl_path.mkdir(parents=True, exist_ok=True)
    with open(pkl_path / "buildings.pkl", "wb") as f:
        f.write(b"corrupt pickle data")
    result = _cache.load_overture_buildings(10.0, 11.0, 20.0, 21.0)
    assert result is None
