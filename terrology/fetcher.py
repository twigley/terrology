import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
from scipy.ndimage import distance_transform_edt

from terrology import cache as _cache

_CONFIG_FILE = Path.home() / ".config" / "terrology" / "config"


def _get_ot_api_key() -> str:
    """Return the OpenTopography API key from env var or config file."""
    import os

    key = os.environ.get("OPENTOPOGRAPHY_API_KEY", "").strip()
    if key:
        return key
    if _CONFIG_FILE.exists():
        for line in _CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "OPENTOPOGRAPHY_API_KEY":
                return v.strip()
    return ""


def ot_key_configured() -> bool:
    """True if an OpenTopography API key is available."""
    return bool(_get_ot_api_key())


def save_ot_api_key(key: str) -> None:
    """Persist the OpenTopography API key to the config file."""
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = _CONFIG_FILE.read_text().splitlines() if _CONFIG_FILE.exists() else []
    new_lines, found = [], False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == "OPENTOPOGRAPHY_API_KEY":
                new_lines.append(f"OPENTOPOGRAPHY_API_KEY={key}")
                found = True
                continue
        new_lines.append(line)
    if not found:
        new_lines.append(f"OPENTOPOGRAPHY_API_KEY={key}")
    _CONFIG_FILE.write_text("\n".join(new_lines) + "\n")


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
    "circuits": {"sport": "motor"},
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

# OpenTopography API demtype codes for alternative sources.
# All require a free API key: https://opentopography.org
_OT_DEMTYPES = {
    "srtm": "SRTMGL1",
    "aw3d30": "AW3D30",
}
_OT_LABELS = {
    "srtm": "SRTM GL1",
    "aw3d30": "AW3D30",
}
DEM_SOURCES = ["glo30", "srtm", "aw3d30"]


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
        except Exception as exc:
            print(f"  WARNING: OSM combined fetch failed: {exc}")
            return gpd.GeoDataFrame()

    def _fetch_coastlines():
        try:
            return ox.features_from_bbox(coast_bbox, tags=OSM_TAGS["coastlines"])
        except Exception as exc:
            print(f"  WARNING: OSM coastline fetch failed: {exc}")
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

    if use_cache and total_features > 0:
        _cache.save_osm(south, north, west, east, result)
    elif use_cache and total_features == 0:
        print("  (skipping cache — 0 features, likely a fetch error)")

    return result


def fetch_elevation(
    south: float,
    north: float,
    west: float,
    east: float,
    use_cache: bool = True,
    dem_source: str = "glo30",
) -> tuple[np.ndarray, dict]:
    """Fetch elevation data for the given bbox.

    dem_source choices:
      'glo30'  — Copernicus GLO-30 via public S3 (no key needed, default)
      'srtm'   — SRTM GL1 30 m via OpenTopography API (free key required)
      'aw3d30' — ALOS AW3D30 30 m via OpenTopography API (free key required)
    """
    if dem_source == "glo30":
        return _fetch_glo30(south, north, west, east, use_cache)
    if dem_source in _OT_DEMTYPES:
        return _fetch_opentopography(south, north, west, east, use_cache, dem_source)
    raise ValueError(f"Unknown dem_source {dem_source!r}. Choose from: {DEM_SOURCES}")


def _fetch_glo30(
    south: float,
    north: float,
    west: float,
    east: float,
    use_cache: bool,
) -> tuple[np.ndarray, dict]:
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


def _fetch_opentopography(
    south: float,
    north: float,
    west: float,
    east: float,
    use_cache: bool,
    dem_source: str,
) -> tuple[np.ndarray, dict]:
    """Fetch elevation via the OpenTopography global DEM API (free key required)."""
    import urllib.error
    import urllib.request

    demtype = _OT_DEMTYPES[dem_source]
    label = _OT_LABELS[dem_source]

    if use_cache:
        cached = _cache.load_elevation(south, north, west, east, demtype)
        if cached is not None:
            arr, header = cached
            print(f"  (cache hit — {arr.shape[1]}x{arr.shape[0]} cells)")
            return arr, header

    api_key = _get_ot_api_key()
    if not api_key:
        raise RuntimeError(
            f"DEM source '{dem_source}' requires an OpenTopography API key.\n"
            f"  Save it once with:  uv run main.py --save-api-key <key>\n"
            f"  Or set the env var: export OPENTOPOGRAPHY_API_KEY=<key>\n"
            f"  Free key at: https://opentopography.org"
        )

    url = (
        "https://portal.opentopography.org/API/globaldem"
        f"?demtype={demtype}"
        f"&south={south:.6f}&north={north:.6f}"
        f"&west={west:.6f}&east={east:.6f}"
        f"&outputFormat=AAIGrid&API_Key={api_key}"
    )

    print(f"  Downloading {label} via OpenTopography...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(
            f"OpenTopography returned HTTP {e.code} for {dem_source}: {body}"
        ) from e

    arr, header = _parse_aaigrid(text)

    if use_cache:
        _cache.save_elevation(south, north, west, east, demtype, arr, header)

    return arr, header


def fetch_overture_buildings(
    south: float,
    north: float,
    west: float,
    east: float,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Fetch Overture Maps building footprints for the given bbox."""
    _EMPTY = gpd.GeoDataFrame(columns=["geometry", "height", "levels"], crs="EPSG:4326")

    if use_cache:
        cached = _cache.load_overture_buildings(south, north, west, east)
        if cached is not None:
            print(f"  (cache hit — {len(cached):,} Overture buildings)")
            return cached

    try:
        import overturemaps
    except ImportError:
        print("  overturemaps not installed — skipping building supplement")
        return _EMPTY

    print("  Fetching Overture buildings...")
    try:
        # stac=True pre-filters parquet files via the STAC catalog so pyarrow only
        # opens the handful of files that actually intersect the bbox, rather than
        # scanning the entire global dataset.
        reader = overturemaps.record_batch_reader(
            "building", bbox=(west, south, east, north), stac=True
        )
        if reader is None:
            print("  Overture reader returned None")
            return _EMPTY
        table = reader.read_all().select(["geometry", "height", "num_floors"])
    except Exception as e:
        print(f"  Overture fetch failed: {e}")
        return _EMPTY

    if len(table) == 0:
        gdf = _EMPTY
    else:
        import shapely

        geoms = shapely.from_wkb(table.column("geometry").to_pylist())
        gdf = gpd.GeoDataFrame(
            {
                "geometry": geoms,
                "height": table.column("height").to_pylist(),
                "levels": table.column("num_floors").to_pylist(),
            },
            crs="EPSG:4326",
        )
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)

    print(f"  {len(gdf):,} Overture buildings")
    if use_cache:
        _cache.save_overture_buildings(south, north, west, east, gdf)
    return gdf


def supplement_buildings(
    osm_gdf: gpd.GeoDataFrame,
    overture_gdf: gpd.GeoDataFrame,
    overlap_threshold: float = 0.4,
) -> gpd.GeoDataFrame:
    """Return osm_gdf supplemented with Overture footprints not already covered by OSM."""
    from shapely import STRtree

    if overture_gdf is None or len(overture_gdf) == 0:
        return osm_gdf
    if osm_gdf is None or len(osm_gdf) == 0:
        result = overture_gdf.copy()
        if "building" not in result.columns:
            result["building"] = "yes"
        return result

    osm_geoms = osm_gdf.geometry.values
    tree = STRtree(osm_geoms)
    ov_geoms = overture_gdf.geometry.values

    keep = []
    for i, geom in enumerate(ov_geoms):
        if geom is None or geom.is_empty:
            continue
        candidates = tree.query(geom, predicate="intersects")
        if len(candidates) == 0:
            keep.append(i)
            continue
        area = geom.area
        if area == 0:
            continue
        overlap = sum(geom.intersection(osm_geoms[j]).area for j in candidates)
        if overlap / area < overlap_threshold:
            keep.append(i)

    if not keep:
        return osm_gdf

    new_rows = overture_gdf.iloc[keep].copy()
    if "building" not in new_rows.columns:
        new_rows["building"] = "yes"

    combined = gpd.GeoDataFrame(
        pd.concat([osm_gdf, new_rows], ignore_index=True),
        crs=osm_gdf.crs,
    )
    print(f"  +{len(new_rows):,} Overture footprints added")
    return combined


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
