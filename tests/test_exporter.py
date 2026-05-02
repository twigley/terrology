import numpy as np
import trimesh


def _box_mesh() -> trimesh.Trimesh:
    return trimesh.creation.box()


# ------------------------------------------------------------------ #
# export_stl
# ------------------------------------------------------------------ #


def test_export_stl_creates_file(tmp_path):
    from terrology.exporter import export_stl

    out = tmp_path / "test.stl"
    export_stl(_box_mesh(), out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_export_stl_triangle_count(tmp_path):
    from terrology.exporter import export_stl

    out = tmp_path / "test.stl"
    mesh = _box_mesh()
    export_stl(mesh, out)
    loaded = trimesh.load(str(out))
    assert len(loaded.faces) == len(mesh.faces)


# ------------------------------------------------------------------ #
# export_obj — basic
# ------------------------------------------------------------------ #


def test_export_obj_creates_both_files(tmp_path):
    from terrology.exporter import export_obj

    out = tmp_path / "model.obj"
    export_obj({"terrain_base": _box_mesh()}, out)
    assert out.exists()
    assert (tmp_path / "model.mtl").exists()


def test_export_obj_mtllib_reference(tmp_path):
    from terrology.exporter import export_obj

    out = tmp_path / "model.obj"
    export_obj({"terrain_base": _box_mesh()}, out)
    content = out.read_text()
    assert "mtllib model.mtl" in content


def test_export_obj_vertex_count(tmp_path):
    from terrology.exporter import export_obj

    out = tmp_path / "model.obj"
    mesh = _box_mesh()
    export_obj({"terrain_base": mesh}, out)
    v_lines = [line for line in out.read_text().splitlines() if line.startswith("v ")]
    assert len(v_lines) == len(mesh.vertices)


def test_export_obj_face_count(tmp_path):
    from terrology.exporter import export_obj

    out = tmp_path / "model.obj"
    mesh = _box_mesh()
    export_obj({"terrain_base": mesh}, out)
    f_lines = [line for line in out.read_text().splitlines() if line.startswith("f ")]
    assert len(f_lines) == len(mesh.faces)


# ------------------------------------------------------------------ #
# export_obj — vertex offset across multiple objects
# ------------------------------------------------------------------ #


def test_export_obj_vertex_offsets(tmp_path):
    from terrology.exporter import export_obj

    m1 = trimesh.creation.box()
    m2 = trimesh.creation.box()
    out = tmp_path / "model.obj"
    export_obj({"terrain_base": m1, "buildings": m2}, out)

    lines = out.read_text().splitlines()
    face_lines = [line for line in lines if line.startswith("f ")]

    indices = []
    for fl in face_lines:
        indices.extend(int(x) for x in fl.split()[1:])

    n_total = len(m1.vertices) + len(m2.vertices)
    assert max(indices) == n_total
    assert min(indices) == 1


# ------------------------------------------------------------------ #
# export_obj — colour-sorted terrain_top
# ------------------------------------------------------------------ #


def _flat_mesh(n=4) -> trimesh.Trimesh:
    x1 = np.linspace(0, 1, n)
    y1 = np.linspace(0, 1, n)
    x, y = np.meshgrid(x1, y1)
    z = np.zeros_like(x)
    from terrology.builder import _heightfield_layer

    return _heightfield_layer(x, y, z_top=z, z_bot=np.full_like(z, -1.0))


def test_export_obj_usemtl_grouping(tmp_path):
    from terrology.exporter import export_obj

    mesh = _flat_mesh(4)
    n = len(mesh.faces)
    half = n // 2
    fc = np.array([0] * half + [1] * (n - half), dtype=np.int32)

    out = tmp_path / "model.obj"
    export_obj({"terrain_top": mesh}, out, terrain_face_colors=fc)

    content = out.read_text()
    usemtl_lines = [line for line in content.splitlines() if line.startswith("usemtl")]
    assert len(usemtl_lines) == 2
    assert usemtl_lines[0] == "usemtl terrain"
    assert usemtl_lines[1] == "usemtl water"


def test_export_obj_single_color_one_usemtl(tmp_path):
    from terrology.exporter import export_obj

    mesh = _flat_mesh(4)
    fc = np.zeros(len(mesh.faces), dtype=np.int32)

    out = tmp_path / "model.obj"
    export_obj({"terrain_top": mesh}, out, terrain_face_colors=fc)

    usemtl_lines = [
        line for line in out.read_text().splitlines() if line.startswith("usemtl")
    ]
    assert len(usemtl_lines) == 1


# ------------------------------------------------------------------ #
# _patch_to_solid — watertight colour STL solids
# ------------------------------------------------------------------ #


def test_patch_to_solid_watertight():
    from terrology.exporter import _patch_to_solid

    mesh = _flat_mesh(6)
    face_indices = np.arange(len(mesh.faces) // 2)
    solid = _patch_to_solid(mesh, face_indices, depth_mm=1.5)
    assert solid is not None
    assert solid.is_watertight


def test_patch_to_solid_z_extent():
    from terrology.exporter import _patch_to_solid

    mesh = _flat_mesh(6)  # top at z=0, bottom at z=-1
    face_indices = np.arange(len(mesh.faces))
    solid = _patch_to_solid(mesh, face_indices, depth_mm=1.5)
    assert solid is not None
    # solid should extend 1.5 mm below the original top surface
    assert solid.bounds[0][2] < mesh.bounds[1][2] - 1.0


def test_export_color_stls_produces_watertight(tmp_path):
    from terrology.exporter import export_color_stls

    mesh = _flat_mesh(6)
    # Assign parks colour to the second half of upward-facing faces only —
    # export_color_stls now filters to top faces, so color on bottom/side faces
    # would be silently ignored and parks.stl would not be created.
    top_idx = np.where(mesh.face_normals[:, 2] > 0.5)[0]
    fc = np.zeros(len(mesh.faces), dtype=np.int32)
    fc[top_idx[len(top_idx) // 2 :]] = 2  # parks

    export_color_stls(mesh, fc, tmp_path, color_depth_mm=1.5)

    parks_stl = tmp_path / "parks.stl"
    assert parks_stl.exists()
    loaded = trimesh.load(str(parks_stl))
    assert loaded.is_watertight


# ------------------------------------------------------------------ #
# export_3mf — standard 3MF with basematerials
# ------------------------------------------------------------------ #


def test_export_3mf_is_valid_zip(tmp_path):
    from terrology.exporter import export_3mf

    out = tmp_path / "model.3mf"
    export_3mf({"terrain_base": _box_mesh()}, out)
    import zipfile

    assert zipfile.is_zipfile(str(out))


def test_export_3mf_required_entries(tmp_path):
    import zipfile

    from terrology.exporter import export_3mf

    out = tmp_path / "model.3mf"
    export_3mf({"terrain_base": _box_mesh()}, out)
    with zipfile.ZipFile(str(out)) as zf:
        names = zf.namelist()
    assert "[Content_Types].xml" in names
    assert "_rels/.rels" in names
    assert "3D/3dmodel.model" in names


def test_export_3mf_basematerials_present(tmp_path):
    import zipfile

    from terrology.exporter import export_3mf

    out = tmp_path / "model.3mf"
    export_3mf({"terrain_base": _box_mesh()}, out)
    with zipfile.ZipFile(str(out)) as zf:
        model = zf.read("3D/3dmodel.model").decode()
    assert "<basematerials" in model
    assert 'name="terrain"' in model
    assert 'name="parks"' in model


def test_export_3mf_no_bambu_extensions(tmp_path):
    import zipfile

    from terrology.exporter import export_3mf

    out = tmp_path / "model.3mf"
    export_3mf({"terrain_base": _box_mesh()}, out)
    with zipfile.ZipFile(str(out)) as zf:
        model = zf.read("3D/3dmodel.model").decode()
    assert "bambu" not in model.lower()
    assert "paint_color" not in model
    assert "BambuStudio" not in model


def test_export_3mf_colour_objects_present(tmp_path):
    """Each colour present in face_colors gets its own named object."""
    import zipfile

    from terrology.exporter import export_3mf

    mesh = _flat_mesh(6)
    n = len(mesh.faces)
    fc = np.array(
        [0] * (n // 3) + [1] * (n // 3) + [2] * (n - 2 * (n // 3)), dtype=np.int32
    )

    out = tmp_path / "model.3mf"
    export_3mf({"terrain_top": mesh}, out, terrain_face_colors=fc)
    with zipfile.ZipFile(str(out)) as zf:
        model = zf.read("3D/3dmodel.model").decode()
    assert 'name="terrain"' in model
    assert 'name="water"' in model
    assert 'name="parks"' in model
    # roads not present in face_colors — should have no object, only a basematerials entry
    assert model.count('name="roads"') == 1  # just the <base> entry, no <object>


def test_export_3mf_objects_use_object_level_p1(tmp_path):
    """Colour is on the object element; triangles should carry no p1."""
    import zipfile

    from terrology.exporter import export_3mf

    mesh = _flat_mesh(4)
    n = len(mesh.faces)
    fc = np.array([0] * (n // 2) + [2] * (n - n // 2), dtype=np.int32)

    out = tmp_path / "model.3mf"
    export_3mf({"terrain_top": mesh}, out, terrain_face_colors=fc)
    with zipfile.ZipFile(str(out)) as zf:
        model = zf.read("3D/3dmodel.model").decode()
    # p1 should appear only on <object> lines, not inside <triangles>
    for tri_block in model.split("<triangles>")[1:]:
        block = tri_block.split("</triangles>")[0]
        assert "p1=" not in block


def test_export_obj_mtl_only_used_colors(tmp_path):
    from terrology.exporter import export_obj

    mesh = _flat_mesh(4)
    # Only terrain (0) and water (1) used
    fc = np.array([0, 1] * (len(mesh.faces) // 2), dtype=np.int32)

    out = tmp_path / "model.obj"
    export_obj({"terrain_top": mesh}, out, terrain_face_colors=fc)

    mtl = (tmp_path / "model.mtl").read_text()
    assert "newmtl terrain" in mtl
    assert "newmtl water" in mtl
    assert "newmtl parks" not in mtl
    assert "newmtl roads" not in mtl
