from pathlib import Path

import numpy as np
import trimesh

# 0=terrain  1=water  2=parks  3=roads  4=buildings  5=route  6=railways  7=sand
COLOUR_NAMES = [
    "terrain",
    "water",
    "parks",
    "roads",
    "buildings",
    "route",
    "railways",
    "sand",
]
_COLOURS_RGB = [
    (180, 160, 130),  # 0 terrain   — stone/sand
    (70, 140, 210),  # 1 water     — blue
    (80, 165, 80),  # 2 parks     — green
    (220, 215, 200),  # 3 roads     — light warm grey
    (235, 220, 195),  # 4 buildings — cream
    (210, 55, 35),  # 5 route     — vivid red
    (70, 65, 60),  # 6 railways  — dark charcoal
    (235, 210, 140),  # 7 sand     — warm golden
]

_OBJECT_MAT = {
    "terrain_base": "terrain",
    "terrain_top": "terrain",  # overridden per-face when terrain_face_colors present
}


def export_color_stls(
    mesh: trimesh.Trimesh,
    face_colors: "np.ndarray",
    out_dir: Path,
    color_depth_mm: float = 1.5,
    prefix: str = "",
) -> None:
    """Export a watertight solid STL for each non-terrain colour.

    Each solid is the colour surface patch extruded straight down by
    color_depth_mm, making it importable as a proper multi-material body
    rather than an open shell that slicers tend to ignore.
    """
    top_face_mask = mesh.face_normals[:, 2] > 0.5
    for idx in np.unique(face_colors):
        if idx == 0:
            continue  # terrain base already in terrain.stl
        name = COLOUR_NAMES[idx]
        face_indices = np.where((face_colors == idx) & top_face_mask)[0]
        solid = _patch_to_solid(mesh, face_indices, color_depth_mm)
        if solid is None or len(solid.faces) == 0:
            continue
        path = out_dir / f"{prefix}{name}.stl"
        solid.export(str(path))
        kb = path.stat().st_size / 1024
        print(f"  {path.name}  ({kb:.0f} KB,  {len(solid.faces):,} triangles)")


def _patch_to_solid(
    mesh: trimesh.Trimesh,
    face_indices: "np.ndarray",
    depth_mm: float,
) -> "trimesh.Trimesh | None":
    """
    Turn a subset of mesh faces (open surface patch) into a watertight solid
    by extruding every vertex straight down by depth_mm, then capping with
    side walls along the patch boundary.
    """
    if len(face_indices) == 0:
        return None

    faces = mesh.faces[face_indices]
    used = np.unique(faces.ravel())
    if len(used) < 3:
        return None

    # Compact vertex indices
    remap = np.full(len(mesh.vertices), -1, dtype=np.intp)
    remap[used] = np.arange(len(used))
    n = len(used)

    top = mesh.vertices[used].copy()
    bot = top.copy()
    bot[:, 2] -= depth_mm
    verts = np.vstack([top, bot])  # top: 0..n-1  bot: n..2n-1

    tf = remap[faces]  # top faces (remapped)
    bf = tf[:, ::-1] + n  # bottom faces (reversed winding)

    # Boundary edges: appear in exactly one face
    all_edges = np.vstack([tf[:, [0, 1]], tf[:, [1, 2]], tf[:, [2, 0]]])
    canon = np.sort(all_edges, axis=1)
    _, inv, cnt = np.unique(canon, axis=0, return_inverse=True, return_counts=True)
    boundary = all_edges[cnt[inv] == 1]  # directed boundary edges

    a, b = boundary[:, 0], boundary[:, 1]
    sf1 = np.column_stack([a, b, n + b])
    sf2 = np.column_stack([a, n + b, n + a])

    solid = trimesh.Trimesh(vertices=verts, faces=np.vstack([tf, bf, sf1, sf2]))
    trimesh.repair.fix_normals(solid)
    return solid


def export_3mf(
    parts: dict,
    path: Path,
    terrain_face_colors: "np.ndarray | None" = None,
    color_depth_mm: float = 1.5,
    n_colors: int = 4,
) -> None:
    """
    Write a standard 3MF file with one object per colour.
    No Bambu or OrcaSlicer proprietary extensions.

    Objects written:
      terrain  — terrain_base_mesh + any terrain-coloured surface region
      water    — water surface solid   (if present)
      parks    — parks surface solid   (if present)
      roads    — roads surface solid   (if present)
      buildings — buildings mesh       (if present)

    Each object carries a uniform pid/p1 at the object level so any
    conformant slicer can assign each to a different filament slot.
    """
    import zipfile

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels"'
        ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model"'
        ' ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel-1"\n'
        '   Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>"
    )
    objects = _3mf_objects(parts, terrain_face_colors, color_depth_mm, n_colors)
    model_xml = _build_3mf_model(objects)

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", model_xml)

    kb = path.stat().st_size / 1024
    names = [name for name, *_ in objects]
    print(f"  {path.name}  ({kb:.0f} KB)  [{', '.join(names)}]")


def _3mf_objects(
    parts: dict,
    terrain_face_colors: "np.ndarray | None",
    color_depth_mm: float,
    n_colors: int = 4,
) -> "list[tuple[str, trimesh.Trimesh, int]]":
    """
    Return (name, mesh, colour_index) tuples — one entry per colour present.
    colour_index is the COLOUR_NAMES index used as p1 in basematerials.
    """
    objects: list[tuple[str, trimesh.Trimesh, int]] = []

    terrain_base = parts.get("terrain_base")
    terrain_top = parts.get("terrain_top")
    buildings = parts.get("buildings")

    # Only use upward-facing faces for colour patches — the terrain_surface_mesh
    # also contains bottom and side-wall faces whose centroids happen to lie inside
    # feature polygons, causing non-manifold edges when extruded.
    top_face_mask = (
        terrain_top.face_normals[:, 2] > 0.5
        if terrain_top is not None
        else np.array([], dtype=bool)
    )

    # Terrain: base slab + any terrain-coloured surface patch
    terrain_parts: list[trimesh.Trimesh] = []
    if terrain_base is not None:
        terrain_parts.append(terrain_base)
    if terrain_top is not None and terrain_face_colors is not None:
        face_idx = np.where((terrain_face_colors == 0) & top_face_mask)[0]
        terrain_patch = _patch_to_solid(terrain_top, face_idx, color_depth_mm)
        if terrain_patch is not None:
            terrain_parts.append(terrain_patch)
    elif terrain_top is not None:
        terrain_parts.append(terrain_top)

    if terrain_parts:
        terrain_mesh = (
            trimesh.util.concatenate(terrain_parts)
            if len(terrain_parts) > 1
            else terrain_parts[0]
        )
        objects.append(("terrain", terrain_mesh, 0))

    # Per-colour surface solids — one object per non-zero colour present in face_colors
    if terrain_top is not None and terrain_face_colors is not None:
        for cidx in np.unique(terrain_face_colors):
            if cidx == 0:
                continue
            face_idx = np.where((terrain_face_colors == cidx) & top_face_mask)[0]
            if len(face_idx) == 0:
                continue
            solid = _patch_to_solid(terrain_top, face_idx, color_depth_mm)
            if solid is not None:
                objects.append((COLOUR_NAMES[cidx], solid, int(cidx)))

    if buildings is not None:
        buildings_cidx = 4 if n_colors >= 5 else 0
        objects.append(("buildings", buildings, buildings_cidx))

    return objects


def _build_3mf_model(
    objects: "list[tuple[str, trimesh.Trimesh, int]]",
) -> str:
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<model unit="millimeter" xml:lang="en-US"\n',
        '       xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n',
        ' <metadata name="Title">Terrology terrain map</metadata>\n',
        " <resources>\n",
        '  <basematerials id="1">\n',
    ]
    max_cidx = max((cidx for _, _, cidx in objects), default=3)
    for i in range(max(4, max_cidx + 1)):
        r, g, b = _COLOURS_RGB[i]
        out.append(
            f'   <base name="{COLOUR_NAMES[i]}" displaycolor="#{r:02X}{g:02X}{b:02X}"/>\n'
        )
    out.append("  </basematerials>\n")

    build_ids: list[int] = []
    obj_id = 2
    for name, mesh, cidx in objects:
        build_ids.append(obj_id)
        out.append(
            f'  <object id="{obj_id}" name="{name}" type="model" pid="1" p1="{cidx}">\n'
        )
        out.append("   <mesh>\n    <vertices>\n")
        out.extend(
            f'     <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>\n'
            for x, y, z in mesh.vertices
        )
        out.append("    </vertices>\n    <triangles>\n")
        out.extend(
            f'     <triangle v1="{v1}" v2="{v2}" v3="{v3}"/>\n'
            for v1, v2, v3 in mesh.faces
        )
        out.append("    </triangles>\n   </mesh>\n  </object>\n")
        obj_id += 1

    out.append(" </resources>\n <build>\n")
    for bid in build_ids:
        out.append(f'  <item objectid="{bid}"/>\n')
    out.append(" </build>\n</model>")
    return "".join(out)


def export_stl(mesh: trimesh.Trimesh, path: Path) -> None:
    mesh.export(str(path))
    kb = path.stat().st_size / 1024
    print(f"  {path.name}  ({kb:.0f} KB,  {len(mesh.faces):,} triangles)")


def export_obj(
    parts: dict,
    path: Path,
    terrain_face_colors: "np.ndarray | None" = None,
    n_colors: int = 4,
) -> None:
    """
    Write a coloured OBJ + MTL file.

    Bambu Studio 1.9.1+ imports OBJ files with MTL material colours natively
    and lets you remap each material to a filament.  This is the simplest
    reliable way to get per-feature colours into Bambu Studio.

    terrain_face_colors: int array (n_terrain_faces,) with values
      0=terrain, 1=water, 2=parks  — painted onto the terrain mesh.
    """
    valid = {k: v for k, v in parts.items() if v is not None}
    mtl_filename = path.stem + ".mtl"
    mtl_path = path.parent / mtl_filename
    buildings_cidx = 4 if n_colors >= 5 else 0

    # Work out which material indices are actually used so we only write those
    used: set[int] = {0}  # terrain always needed
    if terrain_face_colors is not None:
        used.update(int(i) for i in np.unique(terrain_face_colors))
    if buildings_cidx and "buildings" in valid:
        used.add(buildings_cidx)

    # --- MTL file ---
    with open(mtl_path, "w") as f:
        f.write("# map3d material library\n")
        for idx in sorted(used):
            name = COLOUR_NAMES[idx]
            r, g, b = _COLOURS_RGB[idx]
            f.write(f"\nnewmtl {name}\n")
            f.write(f"Kd {r / 255:.4f} {g / 255:.4f} {b / 255:.4f}\n")
            f.write("Ka 0.0000 0.0000 0.0000\n")
            f.write("Ks 0.0000 0.0000 0.0000\n")
            f.write("illum 1\n")

    # --- OBJ file ---
    with open(path, "w") as f:
        f.write("# map3d — generated model\n")
        f.write(f"mtllib {mtl_filename}\n\n")

        v_offset = 0  # running 1-based vertex index offset

        for obj_name, mesh in valid.items():
            f.write(f"o {obj_name}\n")

            np.savetxt(f, mesh.vertices, fmt="v %.4f %.4f %.4f")

            faces_1b = mesh.faces + 1 + v_offset

            if obj_name == "terrain_top" and terrain_face_colors is not None:
                # Sort faces by colour so we minimise usemtl switches
                order = np.argsort(terrain_face_colors, kind="stable")
                sorted_colors = np.asarray(terrain_face_colors)[order]
                sorted_faces = faces_1b[order]
                breaks = np.flatnonzero(np.diff(sorted_colors)) + 1
                starts = np.concatenate([[0], breaks])
                ends = np.concatenate([breaks, [len(sorted_colors)]])
                for s, e in zip(starts, ends):
                    cidx = int(sorted_colors[s])
                    f.write(f"usemtl {COLOUR_NAMES[cidx]}\n")
                    np.savetxt(f, sorted_faces[s:e], fmt="f %d %d %d")
            else:
                mat = (
                    COLOUR_NAMES[buildings_cidx]
                    if obj_name == "buildings"
                    else _OBJECT_MAT.get(obj_name, "terrain")
                )
                f.write(f"usemtl {mat}\n")
                np.savetxt(f, faces_1b, fmt="f %d %d %d")

            v_offset += len(mesh.vertices)
            f.write("\n")

    kb = path.stat().st_size / 1024
    print(f"  {path.name} + {mtl_filename}  ({kb:.0f} KB)  [{', '.join(valid)}]")
