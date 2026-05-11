import math

import geopandas as gpd
import numpy as np
import osmnx as ox
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
    "piers": {"man_made": "pier"},
}

# Copernicus GLO-30: same data as OpenTopography COP30, no key needed, free commercial use.
_GLO30_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"

_GDAL_COG_ENV = {
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_CACHEMAX": 512,
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

    from concurrent.futures import ThreadPoolExecutor

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

    # Merge all non-coastline tags into one combined query (2 Overpass requests
    # total instead of 17) to avoid rate-limiting delays.
    combined_tags: dict = {}
    for name, tags in OSM_TAGS.items():
        if name == "coastlines":
            continue
        for k, v in tags.items():
            if k not in combined_tags:
                combined_tags[k] = v
            elif combined_tags[k] is True or v is True:
                combined_tags[k] = True
            else:
                existing = (
                    combined_tags[k]
                    if isinstance(combined_tags[k], list)
                    else [combined_tags[k]]
                )
                new = v if isinstance(v, list) else [v]
                combined_tags[k] = existing + [x for x in new if x not in existing]

    def _fetch_combined():
        try:
            return ox.features_from_bbox(bbox, tags=combined_tags)
        except Exception:
            return gpd.GeoDataFrame()

    def _fetch_coastlines():
        try:
            return ox.features_from_bbox(coast_bbox, tags=OSM_TAGS["coastlines"])
        except Exception:
            return gpd.GeoDataFrame()

    print("  Fetching OSM features (2 requests)...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        combined_f = executor.submit(_fetch_combined)
        coast_f = executor.submit(_fetch_coastlines)
        combined_gdf = combined_f.result()
        coast_gdf = coast_f.result()

    # Split the combined result back into per-category GeoDataFrames
    result: dict = {}
    for name, tags in OSM_TAGS.items():
        if name == "coastlines":
            result["coastlines"] = coast_gdf
            continue
        gdf = _filter_gdf(combined_gdf, tags)
        if name in ("roads", "railways", "waterways"):
            gdf = _drop_tunnels(gdf)
        result[name] = gdf

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
    use_cache: bool = True,
) -> tuple[np.ndarray, dict]:
    """Fetch Copernicus GLO-30 elevation from public S3 COG tiles (no API key needed)."""
    _DEMTYPE = "GLO30"

    if use_cache:
        cached = _cache.load_elevation(south, north, west, east, _DEMTYPE)
        if cached is not None:
            arr, header = cached
            print(f"  (cache hit — {arr.shape[1]}x{arr.shape[0]} cells)")
            return arr, header

    import rasterio
    import rasterio.env
    import rasterio.merge

    lat_tiles = range(math.floor(south), math.floor(north) + 1)
    lon_tiles = range(math.floor(west), math.floor(east) + 1)

    print(f"  Downloading GLO-30 ({south:.4f},{west:.4f} -> {north:.4f},{east:.4f})...")

    datasets: list = []
    with rasterio.env.Env(**_GDAL_COG_ENV):
        for tlat in lat_tiles:
            for tlon in lon_tiles:
                url = _glo_tile_url(tlat, tlon)
                try:
                    datasets.append(rasterio.open(url))
                except Exception:
                    pass  # coverage gap — NaN fill handles missing tiles

        if not datasets:
            raise RuntimeError(
                f"No GLO-30 tiles found for "
                f"({south:.4f},{west:.4f},{north:.4f},{east:.4f}). "
                "Check network connectivity."
            )

        merged_arr, merged_transform = rasterio.merge.merge(
            datasets, bounds=(west, south, east, north)
        )
        nodata = datasets[0].nodata
        for ds in datasets:
            ds.close()

    arr = merged_arr[0].astype(np.float32)
    if nodata is not None:
        arr[arr == nodata] = np.nan

    if np.any(np.isnan(arr)):
        nan_mask = np.isnan(arr)
        _, indices = distance_transform_edt(nan_mask, return_indices=True)
        arr[nan_mask] = arr[tuple(indices[:, nan_mask])]

    nrows, ncols = arr.shape
    # merged_transform is an Affine: (cellsize_x, 0, xll, 0, -cellsize_y, y_north_edge)
    # GLO-30 uses non-square pixels at higher latitudes: lon step != lat step.
    # Store both so build_terrain can use the correct value for each axis.
    cellsize_x = float(merged_transform.a)
    cellsize_y = float(abs(merged_transform.e))
    xll = float(merged_transform.c)
    yll = float(merged_transform.f + nrows * merged_transform.e)  # south edge

    header = {
        "ncols": float(ncols),
        "nrows": float(nrows),
        "xllcorner": xll,
        "yllcorner": yll,
        "cellsize": cellsize_x,
        "cellsize_y": cellsize_y,
        "nodata_value": -9999.0,
    }

    if use_cache:
        _cache.save_elevation(south, north, west, east, _DEMTYPE, arr, header)

    return arr, header


def _glo_tile_url(tile_lat: int, tile_lon: int) -> str:
    ns = "N" if tile_lat >= 0 else "S"
    ew = "E" if tile_lon >= 0 else "W"
    name = (
        f"Copernicus_DSM_COG_10_{ns}{abs(tile_lat):02d}_00_"
        f"{ew}{abs(tile_lon):03d}_00_DEM"
    )
    return f"/vsicurl/{_GLO30_BASE}/{name}/{name}.tif"


def _filter_gdf(gdf: gpd.GeoDataFrame, tags: dict) -> gpd.GeoDataFrame:
    """Return rows from gdf that match any key/value in tags."""
    if len(gdf) == 0:
        return gdf
    masks = []
    for key, val in tags.items():
        if key not in gdf.columns:
            continue
        col = gdf[key]
        if val is True:
            masks.append(col.notna() & (col != False))  # noqa: E712
        elif isinstance(val, list):
            masks.append(col.isin(val))
        else:
            masks.append(col == val)
    if not masks:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m
    return gdf[combined].copy()


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
