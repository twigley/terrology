"""Decorative geometry: border frame."""

from __future__ import annotations

import trimesh
from shapely.geometry import box as shapely_box


def make_frame_mesh(
    model_x_mm: float,
    model_y_mm: float,
    border_width_mm: float,
    base_z_mm: float,
    top_z_mm: float = 0.0,
    clip_poly_mm=None,
) -> trimesh.Trimesh:
    """Hollow frame around the model footprint.

    When clip_poly_mm is provided the frame follows the clip polygon shape;
    otherwise a rectangle is used.
    """
    if clip_poly_mm is not None:
        inner = clip_poly_mm
        outer = clip_poly_mm.buffer(border_width_mm)
    else:
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
