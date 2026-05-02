# Bambu Studio 3MF format — reverse-engineered spec

Documented from a real Bambu Studio 2.06.01.55 save file and OrcaSlicer source code.
Relevant to any tool that wants to generate `.3mf` files that Bambu Studio accepts natively.

---

## File structure (ZIP)

```
[Content_Types].xml
_rels/.rels
3D/3dmodel.model            ← assembly/metadata (small)
3D/_rels/3dmodel.model.rels ← points to the object file
3D/Objects/object_1.model   ← geometry (large)
Metadata/model_settings.config
Metadata/project_settings.config   ← optional; sets filament colours
Metadata/plate_1.png               ← optional; thumbnail
```

The geometry is split into a separate file referenced via a relationship.  Both model files
use the same XML namespace set.

---

## `[Content_Types].xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
</Types>
```

---

## `_rels/.rels`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1"
   Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
```

---

## `3D/3dmodel.model` — assembly / build manifest

Contains metadata and a single top-level object that references the geometry file via
a component.  No geometry lives here.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
       xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
       xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"
       xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
       requiredextensions="p">
 <metadata name="Application">BambuStudio-02.06.01.55</metadata>
 <metadata name="BambuStudio:3mfVersion">1</metadata>
 <!-- other optional metadata (Copyright, CreationDate, …) -->
 <resources>
  <object id="2" type="model">
   <components>
    <component p:path="/3D/Objects/object_1.model" objectid="1"
               transform="1 0 0 0 1 0 0 0 1 0 0 0"/>
   </components>
  </object>
 </resources>
 <build>
  <item objectid="2" printable="1"/>
 </build>
</model>
```

### "From Bambu Lab" detection

Bambu Studio 2.5+ shows **"not from Bambu Lab, load geometry data only"** unless both of
these `<metadata>` elements are present in `3D/3dmodel.model`:

```xml
<metadata name="Application">BambuStudio-XX.XX.XX.XX</metadata>
<metadata name="BambuStudio:3mfVersion">1</metadata>
```

The exact version string in `Application` does not matter; only the prefix `BambuStudio-`
is checked.

---

## `3D/_rels/3dmodel.model.rels`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/Objects/object_1.model" Id="rel-1"
   Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
```

---

## `3D/Objects/object_1.model` — geometry

Contains the actual mesh.  Multi-colour is encoded **per triangle** using the `paint_color`
attribute (see below), not via separate objects or material groups.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
       xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
       xmlns:BambuStudio="http://schemas.bambulab.com/package/2021"
       xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
 <metadata name="BambuStudio:3mfVersion">1</metadata>
 <resources>
  <object id="1" type="model">
   <mesh>
    <vertices>
     <vertex x="0.0000" y="0.0000" z="-3.0000"/>
     …
    </vertices>
    <triangles>
     <triangle v1="0" v2="1" v3="2" paint_color="4"/>
     <triangle v1="3" v2="4" v3="5" paint_color="1C"/>
     …
    </triangles>
   </mesh>
  </object>
 </resources>
</model>
```

---

## `paint_color` encoding

The `paint_color` hex string on each triangle is a serialized **OrcaSlicer/PrusaSlicer
`TriangleSelector` leaf-node state** (the same format used by support painting and seam
painting, extended to 16 extruder slots for multi-material).

### `EnforcerBlockerType` enum (from `TriangleSelector.hpp`)

```
NONE      = 0   (no paint — inherits parent)
ENFORCER  = 1   = Extruder1
BLOCKER   = 2   = Extruder2
Extruder3 = 3
Extruder4 = 4
…
Extruder16 = 16
```

Extruder 1 and 2 reuse the `ENFORCER`/`BLOCKER` values for backward compatibility with
PrusaSlicer 2.3.1.

### Serialization (leaf triangle, no subdivision)

From `TriangleSelector::serialize()` in `TriangleSelector.cpp`:

```
bitstream = [split_sides & 1, split_sides & 2]   # always [0, 0] for a leaf
          + state_bits
```

**State bits** (depends on `n = extruder_state_value`):

| n | State bits (LSB first) | Total bits |
|---|---|---|
| 0 (NONE) | `[0, 0]` | 4 |
| 1 (Extruder1) | `[1, 0]` | 4 |
| 2 (Extruder2) | `[0, 1]` | 4 |
| 3+ (Extruder3…) | `[1, 1]` + 4 bits of `(n-3)` LSB-first | 8 |

The bitstream is read as a **little-endian integer** (bit[0] = LSB) and formatted as an
**uppercase hex string**, padded to a whole nibble:

- 4-bit values → 1 hex digit (no leading zero)
- 8-bit values → 2 hex digits (leading zero if needed)

### Lookup table (extruders 1–6)

| Extruder | State (n) | Bitstream (LSB→MSB) | Integer | `paint_color` |
|---|---|---|---|---|
| 1 | 1 | `00 10` | 4 | `4` |
| 2 | 2 | `00 01` | 8 | `8` |
| 3 | 3 | `00 11 0000` | 12 | `0C` |
| 4 | 4 | `00 11 1000` | 28 | `1C` |
| 5 | 5 | `00 11 0100` | 44 | `2C` |
| 6 | 6 | `00 11 1100` | 60 | `3C` |

### General formula (Python)

```python
def paint_color(extruder: int) -> str:
    """extruder is 1-indexed (1 = first filament slot)."""
    n = extruder
    if n < 3:
        return format(n * 4, 'X')          # '4' or '8'
    return format(12 + (n - 3) * 16, '02X')  # '0C', '1C', '2C', …
```

---

## `Metadata/model_settings.config`

Per-object settings.  The `object id` must match the `<object id>` in `3D/3dmodel.model`.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2">
    <metadata key="name" value="my model"/>
    <metadata key="extruder" value="1"/>
    <metadata face_count="474884"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="my model"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
      <metadata key="extruder" value="1"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="locked" value="false"/>
  </plate>
</config>
```

The `extruder` value here sets the **base** filament for the object.  Per-triangle
overrides come from `paint_color`.

---

## `Metadata/project_settings.config` (optional)

A large JSON file containing machine profile, filament profiles, and print settings.
Bambu Studio saves the full machine gcode here.  For third-party generators, the
minimum useful subset is `filament_colour`:

```json
{
    "filament_colour": ["#B4A082", "#468CD2", "#50A550", "#DCD7C8"]
}
```

Order corresponds to filament slots 1–4.  Bambu Studio uses these hex colours to
display each painted region in the viewport before slicing.  If the file is absent,
Bambu Studio uses the colours from the loaded filament profiles instead.

---

## Observations and caveats

- **Separate-object approach doesn't work for multi-colour.**  Writing separate `<object>`
  elements (one per colour) and using `<basematerials>` / `pindex` attributes causes
  Bambu Studio 2.5+ to load geometry but show all parts with the default (first) filament
  colour.  The `paint_color` per-triangle approach is required.

- **`requiredextensions="p"`** on the `<model>` element of `3D/3dmodel.model` is needed
  for the Production Extension namespace (`xmlns:p`).  Without it some validators reject
  the file, though Bambu Studio loads it either way.

- **Vertex indices are 0-based** in `<triangle>` elements (same as standard 3MF; OBJ uses
  1-based).

- **Coordinates are in millimetres** with the origin at the model's natural zero; Bambu
  Studio auto-centres on the print plate on import.

- **The geometry object file can be inlined** directly into `3D/3dmodel.model` (skipping
  the `object_1.model` split and the `.rels` file), but Bambu Studio's own exporter always
  uses the split form, so the split form is the safer choice for compatibility.

---

## Unresolved: `project_settings.config` and the preset-name dialog

Including `Metadata/project_settings.config` with a `filament_colour` array causes Bambu
Studio to display the correct per-slot colours in the viewport.  However it also triggers
a **"customised filament or printer presets"** dialog on every import, and filament slots
2–4 are named `model.3mf` (the project filename) rather than something meaningful.

### Root cause

Bambu Studio matches `filament_settings_id` entries against its installed preset database.
When a preset name is not found it creates an embedded preset named after the source file.
The first slot gets its name from `filament_settings_id[0]`; slots 2–4 appear to fall back
to the filename regardless of what `filament_settings_id[1..3]` contains.

Preset names are machine-specific (e.g. `"Bambu PLA Basic @BBL A1"`) so a third-party
generator cannot supply correct names without knowing the user's exact machine model and
installed preset database.

### Things tried that did not help

| Attempt | Result |
|---|---|
| `filament_colour` only | Correct colours, dialog shows `—`, slots unnamed |
| `filament_settings_id: [""]` | Slots named `model.3mf` |
| `filament_settings_id: ["Generic PLA", ...]` | Slots named `model.3mf` |
| `filament_settings_id: ["Terrain","Water","Parks","Roads"]` | Slot 1 named `Terrain(model.3mf)`, rest `model.3mf` |
| `filament_settings_id: ["Terrology"×4]` | Slot 1 named `Terrology(model.3mf)`, rest `model.3mf` |

### Practical outcome

The `paint_color` encoding is correct and colours display properly.  The dialog and naming
are cosmetic nuisances only.  A future fix would require either:
- Detecting the user's Bambu machine model and looking up the corresponding preset name, or
- Bambu Lab exposing a machine-agnostic generic filament preset name that suppresses the dialog.
