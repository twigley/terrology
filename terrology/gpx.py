import xml.etree.ElementTree as ET
from pathlib import Path


def parse_gpx(path: Path) -> list[tuple[float, float]]:
    """Return (lat, lon) pairs from a GPX file's track or route points."""
    tree = ET.parse(path)
    root = tree.getroot()

    # Detect namespace (e.g. http://www.topografix.com/GPX/1/1)
    ns = root.tag[1 : root.tag.index("}")] if root.tag.startswith("{") else ""
    p = f"{{{ns}}}" if ns else ""

    points: list[tuple[float, float]] = []
    for tag in (f"{p}trkpt", f"{p}rtept"):
        for pt in root.iter(tag):
            lat_s, lon_s = pt.get("lat"), pt.get("lon")
            if lat_s is None or lon_s is None:
                continue
            try:
                points.append((float(lat_s), float(lon_s)))
            except ValueError:
                pass
        if points:
            break  # prefer track points; fall back to route points

    if not points:
        raise ValueError(f"No track or route points found in {path}")

    return points
