"""Decorative geometry: border frame, scale bar, text label."""

from __future__ import annotations

import trimesh
from shapely.geometry import box as shapely_box

_SCALE_OPTIONS_M = [50, 100, 200, 250, 500, 1000, 2000, 5000, 10_000]


def make_frame_mesh(
    model_x_mm: float,
    model_y_mm: float,
    border_width_mm: float,
    base_z_mm: float,
    top_z_mm: float = 0.0,
) -> trimesh.Trimesh:
    """Hollow rectangular frame around the model footprint."""
    outer = shapely_box(
        -border_width_mm,
        -border_width_mm,
        model_x_mm + border_width_mm,
        model_y_mm + border_width_mm,
    )
    inner = shapely_box(0, 0, model_x_mm, model_y_mm)
    frame_poly = outer.difference(inner)
    mesh = trimesh.creation.extrude_polygon(frame_poly, top_z_mm - base_z_mm)
    mesh.apply_translation([0, 0, base_z_mm])
    return mesh


def make_scale_bar_mesh(
    mm_per_m: float,
    model_x_mm: float,
    border_width_mm: float,
    top_z_mm: float = 0.0,
    bar_height_mm: float = 0.5,
) -> tuple[trimesh.Trimesh, int]:
    """
    Raised scale bar on the bottom border strip (left side): horizontal bar with
    end tick caps and a distance label to the right.
    Returns (mesh, real_world_metres_represented).
    """
    max_len = model_x_mm / 5.0
    nice_m = max(
        (m for m in _SCALE_OPTIONS_M if m * mm_per_m <= max_len),
        default=_SCALE_OPTIONS_M[0],
    )
    bar_len = nice_m * mm_per_m
    bar_w = max(1.0, border_width_mm * 0.35)

    x_left = -border_width_mm + 2.0
    y_mid = -border_width_mm / 2.0

    meshes: list[trimesh.Trimesh] = []

    # Horizontal bar
    bar = trimesh.creation.box([bar_len, bar_w, bar_height_mm])
    bar.apply_translation(
        [x_left + bar_len / 2.0, y_mid, top_z_mm + bar_height_mm / 2.0]
    )
    meshes.append(bar)

    # Tick caps at each end — taller fins so it reads as a scale bar, not just a rectangle
    cap_h = bar_height_mm + 1.0
    cap_t = max(1.0, bar_w * 0.4)
    for x in [x_left, x_left + bar_len]:
        cap = trimesh.creation.box([cap_t, bar_w, cap_h])
        cap.apply_translation([x, y_mid, top_z_mm + cap_h / 2.0])
        meshes.append(cap)

    # Distance label placed to the right of the bar
    label_text = f"{nice_m // 1000:g} km" if nice_m >= 1000 else f"{nice_m} m"
    font_h = max(1.5, border_width_mm * 0.35)
    label_parts = make_label_meshes(
        label_text,
        model_x_mm=0,
        border_width_mm=border_width_mm,
        top_z_mm=top_z_mm,
        raise_mm=bar_height_mm,
        font_height_mm=font_h,
    )
    if label_parts:
        lmesh = (
            trimesh.util.concatenate(label_parts)
            if len(label_parts) > 1
            else label_parts[0]
        )
        dx = (x_left + bar_len + 1.5) - lmesh.bounds[0][0]
        lmesh.apply_translation([dx, 0, 0])
        meshes.append(lmesh)

    result = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    return result, nice_m


def make_label_meshes(
    text: str,
    model_x_mm: float,
    border_width_mm: float,
    top_z_mm: float = 0.0,
    raise_mm: float = 0.4,
    font_height_mm: float | None = None,
) -> list[trimesh.Trimesh]:
    """
    Raised text on the top of the bottom border strip (right side).

    `text` can contain '\\n' to stack multiple lines; each line is scaled
    independently so the tallest character sets the height.
    """
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    from shapely.affinity import scale as affine_scale
    from shapely.affinity import translate
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    if font_height_mm is None:
        font_height_mm = max(2.0, border_width_mm - 2.0)

    lines = text.split("\n")
    line_spacing = font_height_mm * 1.3
    fp = FontProperties(family="monospace", size=12)

    all_meshes: list[trimesh.Trimesh] = []

    # Vertical: centre the whole block of lines in the border strip
    # Strip runs y=0 → y=-border_width_mm; strip centre = -border_width_mm/2
    block_height = (len(lines) - 1) * line_spacing + font_height_mm
    y_base = -border_width_mm / 2.0 - block_height / 2.0

    for line_idx, line in enumerate(lines):
        if not line.strip():
            continue

        tp = TextPath((0, 0), line, prop=fp, size=12)
        raw = tp.to_polygons()
        if not raw:
            continue

        # Convert to shapely, filter degenerate
        shapely_polys: list[Polygon] = []
        for pts in raw:
            if len(pts) < 3:
                continue
            try:
                p = Polygon(pts)
                if p.is_valid and p.area > 1e-4:
                    shapely_polys.append(p)
            except Exception:
                continue

        if not shapely_polys:
            continue

        # Exterior rings are those not contained by any other ring
        exteriors: list[Polygon] = []
        for i, p in enumerate(shapely_polys):
            if any(
                shapely_polys[j].contains(p)
                for j in range(len(shapely_polys))
                if j != i
            ):
                continue  # this is a hole
            holes = [
                list(shapely_polys[k].exterior.coords)
                for k in range(len(shapely_polys))
                if k != i and p.contains(shapely_polys[k])
            ]
            exteriors.append(Polygon(p.exterior, holes))

        if not exteriors:
            continue

        # Scale to desired font height
        combined = unary_union(exteriors)
        _, miny, _, maxy = combined.bounds
        text_h = maxy - miny
        if text_h <= 0:
            continue
        sf = font_height_mm / text_h

        scaled = [affine_scale(p, xfact=sf, yfact=sf, origin=(0, 0)) for p in exteriors]
        combined_s = unary_union(scaled)
        sminx, sminy, smaxx, _ = combined_s.bounds
        text_w = smaxx - sminx

        # Horizontal: centre each line in the model footprint
        x_offset = (model_x_mm - text_w) / 2.0 - sminx
        y_offset = y_base + line_idx * line_spacing - sminy

        for p in scaled:
            p_pos = translate(p, xoff=x_offset, yoff=y_offset)
            try:
                mesh = trimesh.creation.extrude_polygon(p_pos, raise_mm)
                mesh.apply_translation([0, 0, top_z_mm])
                all_meshes.append(mesh)
            except Exception:
                continue

    return all_meshes
