import hashlib
import json
import pickle
from pathlib import Path

import numpy as np

CACHE_DIR = Path.home() / ".cache" / "3dmap"


def _key(*args) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ #
# OSM
# ------------------------------------------------------------------ #


def _osm_path(south: float, north: float, west: float, east: float) -> Path:
    return (
        CACHE_DIR
        / "osm"
        / _key(round(south, 4), round(north, 4), round(west, 4), round(east, 4))
    )


def load_osm(south: float, north: float, west: float, east: float) -> dict | None:
    pkl = _osm_path(south, north, west, east) / "features.pkl"
    if pkl.exists():
        with open(pkl, "rb") as f:
            return pickle.load(f)
    return None


def save_osm(south: float, north: float, west: float, east: float, data: dict) -> None:
    path = _osm_path(south, north, west, east)
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "features.pkl", "wb") as f:
        pickle.dump(data, f)


# ------------------------------------------------------------------ #
# Elevation
# ------------------------------------------------------------------ #


def _elev_path(south, north, west, east, demtype: str) -> Path:
    return (
        CACHE_DIR
        / "elevation"
        / _key(
            round(south, 5),
            round(north, 5),
            round(west, 5),
            round(east, 5),
            demtype,
        )
    )


def load_elevation(south, north, west, east, demtype: str):
    path = _elev_path(south, north, west, east, demtype)
    npz_file = path / "data.npz"
    hdr_file = path / "header.json"
    if npz_file.exists() and hdr_file.exists():
        arr = np.load(npz_file)["elevation"]
        with open(hdr_file) as f:
            header = json.load(f)
        return arr, header
    return None


def save_elevation(
    south, north, west, east, demtype: str, arr: np.ndarray, header: dict
) -> None:
    path = _elev_path(south, north, west, east, demtype)
    path.mkdir(parents=True, exist_ok=True)
    np.savez(path / "data.npz", elevation=arr)
    with open(path / "header.json", "w") as f:
        json.dump(header, f)


# ------------------------------------------------------------------ #
# Overture buildings
# ------------------------------------------------------------------ #


def _overture_path(south: float, north: float, west: float, east: float) -> Path:
    return (
        CACHE_DIR
        / "overture"
        / _key(round(south, 4), round(north, 4), round(west, 4), round(east, 4))
    )


def load_overture_buildings(south: float, north: float, west: float, east: float):
    pkl = _overture_path(south, north, west, east) / "buildings.pkl"
    if pkl.exists():
        with open(pkl, "rb") as f:
            return pickle.load(f)
    return None


def save_overture_buildings(
    south: float, north: float, west: float, east: float, gdf
) -> None:
    path = _overture_path(south, north, west, east)
    path.mkdir(parents=True, exist_ok=True)
    with open(path / "buildings.pkl", "wb") as f:
        pickle.dump(gdf, f)
