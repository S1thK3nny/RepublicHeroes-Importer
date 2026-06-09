# Republic Heroes Importer

Blender 4.0+ add-on for importing models from **Star Wars The Clone Wars:
Republic Heroes** (PC).

## Installation

1. In Blender: *Edit > Preferences > Add-ons > Install...*
2. Select `io_import_republicheroes_mdl.py`.
3. Enable *"Star Wars The Clone Wars: Republic Heroes (.mdl)"*.

## Usage

*File > Import > Republic Heroes Model (.mdl)*

The importer expects the game's companion files next to the `.mdl`:

| File | Purpose |
| --- | --- |
| `<model>.mdl` | Header: skeleton, material refs, vertex/index layout |
| `<model>.mdg` | Raw geometry (vertex and index buffers) — **required** |
| `<material>.min.bin` | Material definitions (texture names) |
| `<texture>.mktx.tex` | Textures (DXT-compressed) |

Meshes are built with UVs, smooth shading and Principled BSDF node materials
(base color, normal map, specular). The skeleton is imported as an armature
and meshes are bound to it with vertex groups and an Armature modifier.

Import options:

- **Convert Textures** - Use this to convert `.mktx.tex` to `.dds` files written next to
  the sources (off = reuse previously converted files). No need to use Noesis or other external tools to convert textures first.
- **Flip UVs Vertically** - game data stores DirectX-style V; leave on.
- **Import Skeleton** - import with or without the armature (why would you not want the skeleton?).

## Format notes

- `.mktx.tex` files are raw DXT data with a 48-byte footer holding the
  dimensions and a format id. Mapping (verified pixel-level against known
  reference conversions): id **1** = ATI2/BC5 (two-channel normal maps),
  other 16-bytes-per-block ids (0, 16) = DXT5, everything else = DXT1.
- BC5 normal maps only store X/Y; the importer adds a node chain that
  reconstructs Z (`sqrt(1 - x² - y²)`) before the Normal Map node.
- Bone matrices are stored row-major (translation in the last row) and are
  transposed on import for Blender's column-vector convention.
- Skin weights are per-vertex byte quadruplets (`/255`), with per-submesh
  bone palettes mapping skin indices to skeleton bone indices.

## Tests

Headless smoke tests (adjust the Blender path as needed):

```powershell
& "D:\SteamLibrary\steamapps\common\Blender\blender.exe" --background --factory-startup --python tests\run_blender_test.py
& "D:\SteamLibrary\steamapps\common\Blender\blender.exe" --background --factory-startup --python tests\run_texture_test.py
& "D:\SteamLibrary\steamapps\common\Blender\blender.exe" --background --factory-startup --python tests\run_compare_test.py
```

- `run_blender_test.py` builds a synthetic `.mdl`/`.mdg`/`.min.bin` set (one
  triangle, one bone, one material referencing a real sample texture), runs
  the import operator, and asserts on the resulting mesh, UVs, armature,
  parenting, material and converted texture.
- `run_texture_test.py` converts every `.mktx.tex` sample in `tests/samples/`
  and verifies Blender loads each resulting DDS at the expected dimensions.
- `run_compare_test.py` compares each conversion pixel-by-pixel against
  known-good reference conversions (`tests/samples/*.mktxout.tga`).
