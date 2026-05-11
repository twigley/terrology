import pytest

from terrology.decorations import (
    make_frame_mesh,
    make_label_meshes,
    make_scale_bar_mesh,
)


def test_frame_mesh_is_watertight():
    mesh = make_frame_mesh(190, 190, border_width_mm=6, base_z_mm=-3.0)
    assert mesh.is_watertight


def test_frame_mesh_outer_dimensions():
    bw = 6.0
    mesh = make_frame_mesh(190, 100, border_width_mm=bw, base_z_mm=-3.0, top_z_mm=0.0)
    # Outer footprint should be model + 2 * border_width in each direction
    assert pytest.approx(mesh.bounds[0][0], abs=0.1) == -bw
    assert pytest.approx(mesh.bounds[1][0], abs=0.1) == 190 + bw
    assert pytest.approx(mesh.bounds[0][2], abs=0.1) == -3.0
    assert pytest.approx(mesh.bounds[1][2], abs=0.1) == 0.0


def test_scale_bar_mesh_has_geometry():
    # Result is a concatenation of bar + caps + label glyphs — not a single watertight solid
    mesh, m = make_scale_bar_mesh(mm_per_m=0.2, model_x_mm=190, border_width_mm=6)
    assert len(mesh.faces) > 0
    assert len(mesh.vertices) > 0


def test_scale_bar_picks_nice_distance():
    # At scale 1:5000, mm_per_m=0.2, max_len=190/5=38mm
    # 100m → 20mm ✓, 200m → 40mm ✗ → should pick 100m
    _, m = make_scale_bar_mesh(mm_per_m=0.2, model_x_mm=190, border_width_mm=6)
    assert m in (50, 100, 200, 250, 500, 1000, 2000, 5000, 10_000)


def test_scale_bar_fits_within_model_width():
    mm_per_m = 0.2
    model_x = 190.0
    mesh, m = make_scale_bar_mesh(
        mm_per_m=mm_per_m, model_x_mm=model_x, border_width_mm=6
    )
    bar_len = m * mm_per_m
    assert bar_len <= model_x / 5.0 + 0.01


def test_label_meshes_returns_list():
    meshes = make_label_meshes("SOUTHPORT", model_x_mm=190, border_width_mm=6)
    assert isinstance(meshes, list)
    assert len(meshes) > 0


def test_label_meshes_multiline():
    meshes = make_label_meshes("LINE1\nLINE2", model_x_mm=190, border_width_mm=8)
    assert len(meshes) > 0


def test_label_meshes_empty_string():
    meshes = make_label_meshes("", model_x_mm=190, border_width_mm=6)
    assert meshes == []
