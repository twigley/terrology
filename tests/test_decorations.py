import pytest

from terrology.decorations import make_frame_mesh


def test_frame_mesh_is_watertight():
    mesh = make_frame_mesh(190, 190, border_width_mm=6, base_z_mm=-3.0)
    assert mesh.is_watertight


def test_frame_mesh_outer_dimensions():
    bw = 6.0
    mesh = make_frame_mesh(190, 100, border_width_mm=bw, base_z_mm=-3.0, top_z_mm=0.0)
    assert pytest.approx(mesh.bounds[0][0], abs=0.1) == -bw
    assert pytest.approx(mesh.bounds[1][0], abs=0.1) == 190 + bw
    assert pytest.approx(mesh.bounds[0][2], abs=0.1) == -3.0
    assert pytest.approx(mesh.bounds[1][2], abs=0.1) == 0.0
