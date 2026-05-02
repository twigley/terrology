import geopandas as gpd
import numpy as np
import osmnx as ox
import requests
from scipy.ndimage import distance_transform_edt

from terrology import cache as _cache

OSM_TAGS = {
    "buildings": {"building": True},
    "building_parts": {"building:part": True},
    "roads": {"highway": True},
    "railways": {
        "railway": ["rail", "tram", "subway", "light_rail", "narrow_gauge", "monorail"]
    },
    "water_area": {"natural": ["water", "bay"]},
    "waterways": {"waterway": True},
    "water_landuse": {"landuse": ["reservoir", "basin", "salt_pond"]},
    "coastlines": {"natural": "coastline"},
    "parks": {"leisure": "park"},
    "landuse_green": {
        "landuse": [
            "grass",
            "forest",
            "meadow",
            "recreation_ground",
            "greenfield",
            "village_green",
            "allotments",
        ]
    },
    "natural_green": {"natural": ["wood", "scrub", "heath", "grassland", "fell"]},
    "leisure_green": {"leisure": ["pitch", "golf_course", "stadium", "sports_centre"]},
    "cemeteries": {"landuse": "cemetery", "amenity": "grave_yard"},
    "aeroways": {"aeroway": ["runway", "taxiway", "apron"]},
    "parking": {"amenity": "parking"},
    "pedestrian_areas": {"place": "square"},
    "sand": {"natural": ["beach", "sand"], "landuse": "beach"},
}


def fetch_osm_data(
    south: float,
    north: float,
    west: float,
    east: float,
    use_cache: bool = True,
) -> dict:
    if use_cache:
        cached = _cache.load_osm(south, north, west, east)
        if cached is not None:
            total = sum(len(v) for v in cached.values() if v is not None)
            print(f"  (cache hit — {total} features across {len(cached)} layers)")
            return cached

    from concurrent.futures import ThreadPoolExecutor, as_completed

    from tqdm import tqdm

    bbox = (west, south, east, north)  # osmnx 2.x: (left, bottom, right, top)

    # Coastline ways can be hundreds of km long; Overpass only returns a way when
    # at least one node falls inside the queried bbox.  Use a padded bbox so we
    # don't miss ways whose nearest node is just outside the model area.
    _COAST_PAD = 0.02  # degrees (~2 km)
    coast_bbox = (
        west - _COAST_PAD,
        south - _COAST_PAD,
        east + _COAST_PAD,
        north + _COAST_PAD,
    )

    def _fetch(name, tags):
        fetch_bbox = coast_bbox if name == "coastlines" else bbox
        try:
            gdf = ox.features_from_bbox(fetch_bbox, tags=tags)
            return name, gdf, len(gdf)
        except Exception as e:
            return name, gpd.GeoDataFrame(), str(e)

    result = {}
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(_fetch, name, tags): name for name, tags in OSM_TAGS.items()
        }
        with tqdm(
            total=len(OSM_TAGS), desc="  OSM", unit="layer", ncols=72, leave=False
        ) as pbar:
            for future in as_completed(futures):
                name, gdf, info = future.result()
                if name in ("roads", "railways", "waterways"):
                    gdf = _drop_tunnels(gdf)
                result[name] = gdf
                pbar.set_postfix(layer=name, refresh=False)
                pbar.update()

    total_features = sum(len(v) for v in result.values() if v is not None)
    print(f"  {total_features:,} features across {len(result)} layers")

    if use_cache:
        _cache.save_osm(south, north, west, east, result)

    return result


def fetch_elevation(
    south: float,
    north: float,
    west: float,
    east: float,
    api_key: str,
    demtype: str = "COP30",
    use_cache: bool = True,
) -> tuple[np.ndarray, dict]:
    if use_cache:
        cached = _cache.load_elevation(south, north, west, east, demtype)
        if cached is not None:
            arr, header = cached
            print(f"  (cache hit — {arr.shape[1]}x{arr.shape[0]} cells)")
            return arr, header

    url = "https://portal.opentopography.org/API/globaldem"
    params: dict[str, str | float] = {
        "demtype": demtype,
        "south": round(south, 6),
        "north": round(north, 6),
        "west": round(west, 6),
        "east": round(east, 6),
        "outputFormat": "AAIGrid",
        "API_Key": api_key,
    }
    print(
        f"  Downloading {demtype} ({south:.4f},{west:.4f} -> {north:.4f},{east:.4f})..."
    )
    resp = requests.get(url, params=params, timeout=180)
    if not resp.ok:
        raise RuntimeError(
            f"OpenTopography error {resp.status_code}: {resp.text[:300]}"
        )

    arr, header = _parse_aaigrid(resp.text)

    if use_cache:
        _cache.save_elevation(south, north, west, east, demtype, arr, header)

    return arr, header


def _drop_tunnels(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if "tunnel" not in gdf.columns:
        return gdf
    keep = gdf["tunnel"].isna() | (gdf["tunnel"] == "no") | (gdf["tunnel"] == False)  # noqa: E712
    return gdf[keep]


def _parse_aaigrid(text: str) -> tuple[np.ndarray, dict]:
    lines = text.strip().splitlines()
    header: dict[str, float] = {}
    header_keys = {
        "ncols",
        "nrows",
        "xllcorner",
        "yllcorner",
        "xllcenter",
        "yllcenter",
        "cellsize",
        "nodata_value",
    }
    i = 0
    while i < len(lines):
        parts = lines[i].strip().split()
        if parts and parts[0].lower() in header_keys:
            header[parts[0].lower()] = float(parts[1])
            i += 1
        else:
            break

    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    flat = np.fromstring(" ".join(lines[i:]), dtype=np.float32, sep=" ")
    arr = flat.reshape(nrows, ncols)
    nodata = header.get("nodata_value", -9999.0)
    arr[arr == nodata] = np.nan

    if np.any(np.isnan(arr)):
        nan_mask = np.isnan(arr)
        _, indices = distance_transform_edt(nan_mask, return_indices=True)
        arr[nan_mask] = arr[tuple(indices[:, nan_mask])]

    return arr, header
